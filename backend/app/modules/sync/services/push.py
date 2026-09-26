"""Apply a batch of operations pushed by a device (docs/06 §4).

Each operation runs in its own savepoint, so a poisoned one cannot take the
batch down, and each gets its own outcome. The rules the device can rely on:

* **Idempotent.** The delivery's client-generated id is the key. Pushing an
  operation twice — after a dropped connection, a killed app — records it once
  and answers `duplicate` with the stored record, which the device treats as
  success.
* **The server never trusts what the device cannot know.** Rates, conversion
  factors and rules are resolved on the server; the payload has no field for
  them (deliveries/schemas.py).
* **Server wins.** A correction for an entry already reviewed comes back as
  `conflict` with the server's record.
* **Never all-or-nothing.** One entry that cannot be accepted does not hold up
  the others behind it on a poor connection.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.errors import (
    AppError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.types import utcnow
from app.modules.deliveries.schemas import DeliveryCreate
from app.modules.deliveries.services import ingest, review
from app.modules.identity.services import devices
from app.modules.sync.schemas import (
    OpError,
    OpResult,
    PushRequest,
    PushResponse,
    RecordFlag,
    RecordSummary,
    SyncOp,
)

log = get_logger("sync")

PERM_PUSH = ingest.PERM_CREATE
PRICE_PERMISSION = "rates.view"
# A correction that arrives after head office has moved on: the server's
# version stands and the device is told so.
_SERVER_WINS = ("delivery_not_editable", "delivery_not_yours")


def _summary(delivery: Any, ctx: AccessContext) -> RecordSummary:
    show_prices = ctx.has(PRICE_PERMISSION)
    priced = [i.amount for i in delivery.items if i.amount is not None]
    first = delivery.items[0] if delivery.items else None
    return RecordSummary(
        id=delivery.id,
        delivery_number=delivery.delivery_number,
        status=delivery.status,
        flags=[
            RecordFlag(type=f.flag_type, severity=f.severity, message=f.message)
            for f in delivery.flags
            if f.status != "CORRECTED"
        ],
        rate=str(first.rate) if show_prices and first and first.rate is not None else None,
        amount=str(sum(priced)) if show_prices and priced else None,
    )


def _rejected(
    op: SyncOp, code: str, message: str, fields: list[dict[str, str]] | None = None
) -> OpResult:
    return OpResult(
        op_id=op.op_id,
        outcome="rejected",
        error=OpError(code=code, message=message, retryable=False, fields=fields or []),
    )


def _rule_of(exc: AppError) -> str:
    return str(exc.extra.get("rule") or "business_rule")


def _build_input(op: SyncOp, request: PushRequest) -> tuple[DeliveryCreate, ingest.DeliveryInput]:
    payload = DeliveryCreate.model_validate(op.payload)
    data = ingest.input_from(payload)
    # What the device knows about itself comes from the batch, so an entry
    # cannot claim a different device or clock than the one that sent it. An
    # entry pushed through here has by definition waited to be sent.
    return payload, replace(
        data,
        captured_at=payload.captured_at or op.client_created_at or data.captured_at,
        device_id=request.device_id,
        app_version=request.app_version,
        device_time=request.device_time or data.device_time,
        was_offline=True,
    )


async def _apply(
    session: AsyncSession,
    ctx: AccessContext,
    op: SyncOp,
    payload: DeliveryCreate,
    data: ingest.DeliveryInput,
) -> OpResult:
    if op.entity == "delivery" and op.op == "create":
        result = await ingest.ingest(session, ctx, data)
        return OpResult(
            op_id=op.op_id,
            outcome="duplicate" if result.duplicate else "applied",
            record=_summary(result.delivery, ctx),
        )
    if op.entity == "delivery_correction" and op.op == "update":
        if payload.id is None:
            return _rejected(op, "invalid_payload", "A correction must name the delivery (id).")
        corrected = await ingest.correct(session, ctx, payload.id, data)
        await review.record_correction(session, ctx, corrected)
        return OpResult(op_id=op.op_id, outcome="applied", record=_summary(corrected.delivery, ctx))
    return _rejected(op, "unsupported_operation", f"'{op.op}' is not supported for '{op.entity}'.")


async def apply_op(
    session: AsyncSession, ctx: AccessContext, request: PushRequest, op: SyncOp
) -> OpResult:
    try:
        payload, data = _build_input(op, request)
    except PydanticValidationError as exc:
        return _rejected(
            op,
            "invalid_payload",
            "The entry is not valid.",
            [
                {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
                for e in exc.errors()
            ],
        )

    try:
        async with session.begin_nested():
            return await _apply(session, ctx, op, payload, data)
    except BusinessRuleError as exc:
        rule = _rule_of(exc)
        if rule in _SERVER_WINS and payload.id is not None:
            current = await ingest.get_or_404(session, ctx, payload.id)
            return OpResult(
                op_id=op.op_id,
                outcome="conflict",
                record=_summary(current, ctx),
                error=OpError(
                    code="already_reviewed",
                    message="This entry was already handled at head office; the server's "
                    "version stands.",
                    retryable=False,
                ),
            )
        return _rejected(op, rule, exc.detail)
    except ValidationError as exc:
        return _rejected(
            op,
            "validation_failed",
            exc.detail,
            [
                {"field": str(e.get("field", "")), "message": str(e.get("message", ""))}
                for e in exc.errors
            ],
        )
    except NotFoundError as exc:
        entity = str(exc.extra.get("entity", "record"))
        if entity == "Site":
            return _rejected(op, "site_not_permitted", "You are not assigned to this site.")
        return _rejected(op, f"{entity.lower()}_not_found", exc.detail)
    except PermissionDeniedError as exc:
        return _rejected(op, "permission_denied", exc.detail)
    except ConflictError as exc:
        return _rejected(op, "id_conflict", exc.detail)
    except Exception:
        log.exception("sync.op_failed", op_id=op.op_id, entity=op.entity)
        return OpResult(
            op_id=op.op_id,
            outcome="deferred",
            error=OpError(
                code="server_error",
                message="The server could not process this entry right now. Try again shortly.",
                retryable=True,
            ),
        )


async def push(session: AsyncSession, ctx: AccessContext, request: PushRequest) -> PushResponse:
    # A revoked phone is refused here, before it can write anything.
    await devices.check_in(
        session,
        user_id=ctx.user_id,
        device_uid=request.device_id,
        platform=request.platform,
        app_version=request.app_version,
        push_token=request.push_token,
        create=True,
    )
    results = [await apply_op(session, ctx, request, op) for op in request.ops]
    return PushResponse(server_time=utcnow(), results=results)
