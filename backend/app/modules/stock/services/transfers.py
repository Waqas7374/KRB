"""Stock transfers: material moving between stores, possibly between sites.

Two ledger rows per line, written at two different moments:

* **Dispatch** takes the stock out of the source at its current average cost
  and records that cost on the line. The destination shows the quantity as
  *in transit*: not on hand, not lost.
* **Receive** counts it in at the destination **at the cost it left at**, so a
  transfer never changes the value of anything — it only moves it.

Until it is received, a transfer can be cancelled and the goods go back to the
source at their original cost. Once received it cannot: the stock may already
have been used, and the honest way to undo it is a transfer back.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import BusinessRuleError
from app.core.pagination import PageParams
from app.core.scoping import assert_in_scope, scope_filter
from app.core.types import today_utc, utcnow
from app.modules.inventory.domain.enums import TxnType
from app.modules.inventory.services import ledger
from app.modules.masterdata.services import warehouse_lookup
from app.modules.masterdata.services.conversion import UnitConverter
from app.modules.stock.domain.enums import TransferStatus
from app.modules.stock.models import StockTransfer, StockTransferItem
from app.modules.stock.schemas import TransferCreate
from app.modules.stock.services import common
from app.platform.numbering import DocumentType

PERM_VIEW = "inventory.view"
PERM_TRANSFER = "inventory.transfer"


class TransferRepository(ScopedRepository[StockTransfer]):
    """A transfer is visible at both ends: the site it leaves and the site it is
    going to. Its `site_id` (the source) scopes it for everything else."""

    def base_query(self, ctx: AccessContext, permission: str) -> Select[Any]:
        outgoing = scope_filter(select(StockTransfer.id), StockTransfer, ctx, permission)
        scope = ctx.scope_for(permission)
        if scope.is_company_wide or not (scope.site_ids or scope.project_ids):
            return select(StockTransfer).where(StockTransfer.id.in_(outgoing))
        incoming = select(StockTransfer.id).where(
            StockTransfer.company_id.in_(ctx.company_ids),
            (StockTransfer.to_site_id.in_(scope.site_ids))
            | (StockTransfer.to_project_id.in_(scope.project_ids)),
        )
        return select(StockTransfer).where(StockTransfer.id.in_(outgoing.union(incoming)))


def repository(session: AsyncSession) -> TransferRepository:
    return TransferRepository(
        session,
        StockTransfer,
        entity_name="Stock transfer",
        sortable={"transfer_number", "status", "transfer_date", "created_at", "updated_at"},
        searchable=("transfer_number", "vehicle_number"),
        default_sort="-created_at",
    )


def _at_source(ctx: AccessContext, transfer: StockTransfer) -> None:
    assert_in_scope(
        ctx,
        PERM_TRANSFER,
        company_id=transfer.company_id,
        project_id=transfer.project_id,
        site_id=transfer.site_id,
        entity="Stock transfer",
    )


def _at_destination(ctx: AccessContext, transfer: StockTransfer) -> None:
    assert_in_scope(
        ctx,
        PERM_TRANSFER,
        company_id=transfer.company_id,
        project_id=transfer.to_project_id,
        site_id=transfer.to_site_id,
        entity="Stock transfer",
    )


async def _get_for_update(
    session: AsyncSession, ctx: AccessContext, transfer_id: UUID
) -> StockTransfer:
    transfer = await repository(session).get_for_update(ctx, PERM_TRANSFER, transfer_id)
    await session.refresh(transfer, attribute_names=["items"])
    return transfer


async def create(session: AsyncSession, ctx: AccessContext, data: TransferCreate) -> StockTransfer:
    source = await common.warehouse_in_scope(
        session, ctx, data.from_warehouse_id, PERM_TRANSFER, field="from_warehouse_id"
    )
    # The destination need not be somewhere the sender may act: sending stock to
    # another site is the point. It only has to exist.
    destination = await warehouse_lookup.get(
        session, company_id=ctx.company_id, warehouse_id=data.to_warehouse_id
    )
    if destination is None:
        raise common.fail("to_warehouse_id", "Unknown warehouse")

    materials = await common.stockable_materials(
        session, ctx, [line.material_id for line in data.lines]
    )
    on = data.transfer_date or today_utc()
    converter = UnitConverter(session, ctx.company_id)
    for line in data.lines:
        await common.to_base(
            converter,
            quantity=line.quantity,
            unit_id=line.unit_id,
            material=materials[line.material_id],
            on=on,
        )

    transfer = StockTransfer(
        company_id=ctx.company_id,
        transfer_number=await common.next_doc_number(session, ctx, DocumentType.STOCK_TRANSFER),
        status=TransferStatus.DRAFT.value,
        from_warehouse_id=source.id,
        to_warehouse_id=destination.id,
        project_id=source.project_id,
        site_id=source.site_id,
        to_project_id=destination.project_id,
        to_site_id=destination.site_id,
        transfer_date=on,
        vehicle_number=data.vehicle_number,
        remarks=data.remarks,
        created_by_id=ctx.user_id,
        items=[
            StockTransferItem(
                line_no=index + 1,
                material_id=line.material_id,
                quantity=line.quantity,
                unit_id=line.unit_id,
                remarks=line.remarks,
                created_by_id=ctx.user_id,
            )
            for index, line in enumerate(data.lines)
        ],
    )
    session.add(transfer)
    await session.flush()
    return transfer


async def dispatch(session: AsyncSession, ctx: AccessContext, transfer_id: UUID) -> StockTransfer:
    transfer = await _get_for_update(session, ctx, transfer_id)
    _at_source(ctx, transfer)
    if transfer.status != TransferStatus.DRAFT.value:
        raise BusinessRuleError(
            "transfer_not_draft",
            f"{transfer.transfer_number} is {common.human(transfer.status)}; "
            "only a draft can be dispatched.",
        )
    materials = await common.stockable_materials(
        session, ctx, [i.material_id for i in transfer.items]
    )
    converter = UnitConverter(session, ctx.company_id)
    for item in transfer.items:
        base = await common.to_base(
            converter,
            quantity=item.quantity,
            unit_id=item.unit_id,
            material=materials[item.material_id],
            on=transfer.transfer_date,
        )
        txn = await ledger.post(
            session,
            ctx,
            ledger.MovementRequest(
                txn_type=TxnType.TRANSFER_OUT,
                warehouse_id=transfer.from_warehouse_id,
                material_id=item.material_id,
                quantity=base,
                source_type="STOCK_TRANSFER",
                source_id=transfer.id,
                source_line_id=item.id,
                transaction_date=transfer.transfer_date,
                remarks=transfer.transfer_number,
            ),
        )
        item.base_quantity = base
        item.unit_cost = txn.unit_cost
        item.out_txn_id = txn.id
        await ledger.add_in_transit(
            session,
            ctx,
            warehouse_id=transfer.to_warehouse_id,
            material_id=item.material_id,
            quantity=base,
        )
    transfer.status = TransferStatus.IN_TRANSIT.value
    transfer.dispatched_at = utcnow()
    transfer.dispatched_by_id = ctx.user_id
    transfer.version += 1
    transfer.updated_by_id = ctx.user_id
    await session.flush()
    return transfer


async def receive(session: AsyncSession, ctx: AccessContext, transfer_id: UUID) -> StockTransfer:
    transfer = await _get_for_update(session, ctx, transfer_id)
    _at_destination(ctx, transfer)
    if transfer.status != TransferStatus.IN_TRANSIT.value:
        raise BusinessRuleError(
            "transfer_not_in_transit",
            f"{transfer.transfer_number} is {common.human(transfer.status)}; "
            "only a transfer in transit can be received.",
        )
    for item in transfer.items:
        assert item.base_quantity is not None and item.unit_cost is not None
        txn = await ledger.post(
            session,
            ctx,
            ledger.MovementRequest(
                txn_type=TxnType.TRANSFER_IN,
                warehouse_id=transfer.to_warehouse_id,
                material_id=item.material_id,
                quantity=item.base_quantity,
                # At the cost it left at: moving stock never changes its value.
                unit_cost=item.unit_cost,
                source_type="STOCK_TRANSFER",
                source_id=transfer.id,
                source_line_id=item.id,
                transaction_date=today_utc(),
                remarks=transfer.transfer_number,
            ),
        )
        item.in_txn_id = txn.id
        await ledger.add_in_transit(
            session,
            ctx,
            warehouse_id=transfer.to_warehouse_id,
            material_id=item.material_id,
            quantity=-item.base_quantity,
        )
    transfer.status = TransferStatus.RECEIVED.value
    transfer.received_at = utcnow()
    transfer.received_by_id = ctx.user_id
    transfer.version += 1
    transfer.updated_by_id = ctx.user_id
    await session.flush()
    return transfer


async def cancel(
    session: AsyncSession, ctx: AccessContext, transfer_id: UUID, reason: str
) -> StockTransfer:
    transfer = await _get_for_update(session, ctx, transfer_id)
    _at_source(ctx, transfer)
    if transfer.status == TransferStatus.CANCELLED.value:
        raise BusinessRuleError(
            "transfer_cancelled", f"{transfer.transfer_number} is already cancelled."
        )
    if transfer.status == TransferStatus.RECEIVED.value:
        raise BusinessRuleError(
            "transfer_received",
            f"{transfer.transfer_number} has been received, and the stock may already be in use. "
            "Send it back with a new transfer instead.",
        )
    if len(reason.strip()) < 5:
        raise common.fail("reason", "Say why this transfer is being cancelled")
    if transfer.status == TransferStatus.IN_TRANSIT.value:
        # The goods never arrived: back to the source at the cost they left at.
        for item in sorted(transfer.items, key=lambda i: i.line_no, reverse=True):
            if item.out_txn_id is not None:
                await ledger.reverse_by_id(
                    session,
                    ctx,
                    item.out_txn_id,
                    source_type="STOCK_TRANSFER_CANCEL",
                    source_id=transfer.id,
                    remarks=f"{transfer.transfer_number} cancelled",
                )
            if item.base_quantity is not None:
                await ledger.add_in_transit(
                    session,
                    ctx,
                    warehouse_id=transfer.to_warehouse_id,
                    material_id=item.material_id,
                    quantity=-item.base_quantity,
                )
    transfer.status = TransferStatus.CANCELLED.value
    transfer.cancelled_at = utcnow()
    transfer.cancel_reason = reason.strip()
    transfer.version += 1
    transfer.updated_by_id = ctx.user_id
    await session.flush()
    return transfer


async def list_transfers(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    search: str | None,
    filters: dict[str, Any],
) -> tuple[list[StockTransfer], int]:
    return await repository(session).list(ctx, PERM_VIEW, page=page, search=search, filters=filters)


async def get(session: AsyncSession, ctx: AccessContext, transfer_id: UUID) -> StockTransfer:
    return await repository(session).get(ctx, PERM_VIEW, transfer_id)
