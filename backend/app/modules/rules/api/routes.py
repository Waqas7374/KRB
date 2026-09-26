"""Business-rule endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query

from app.api.deps import Access, PageDep, SessionDep, UowDep, require
from app.core.pagination import Page
from app.core.types import utcnow
from app.modules.rules.domain.enums import RuleType
from app.modules.rules.domain.rule_types import RULE_TYPES
from app.modules.rules.models import BusinessRule
from app.modules.rules.schemas import (
    ResolveRequest,
    ResolveResponse,
    RuleCreate,
    RuleRead,
    RuleTypeRead,
    RuleUpdate,
    VerdictRead,
)
from app.modules.rules.services import resolver, rule_service

router = APIRouter(prefix="/business-rules", tags=["business-rules"])

IfMatch = Annotated[int | None, Header(alias="If-Match", description="The version you loaded")]


def _read(row: BusinessRule) -> RuleRead:
    view = RuleRead.model_validate(row)
    spec = RULE_TYPES.get(RuleType(row.rule_type))
    view.rule_type_label = spec.label if spec else None
    return view


@router.get("/types", response_model=list[RuleTypeRead], dependencies=[require("settings.view")])
async def rule_types() -> list[RuleTypeRead]:
    """What each rule type means, where it may be scoped, and the shape of its value."""
    return [
        RuleTypeRead(
            rule_type=spec.rule_type.value,
            label=spec.label,
            description=spec.description,
            scope_keys=sorted(spec.scope_keys),
            example=spec.example,
            value_schema=spec.value_model.model_json_schema(),
        )
        for spec in RULE_TYPES.values()
    ]


@router.get("", response_model=Page[RuleRead], dependencies=[require(rule_service.PERM_VIEW)])
async def list_rules(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: str | None = None,
    rule_type: Annotated[list[str] | None, Query()] = None,
    is_active: bool | None = None,
) -> Page[RuleRead]:
    rows, total = await rule_service.list_rules(
        session,
        ctx,
        page=page,
        search=q,
        filters={"rule_type": rule_type, "is_active": is_active},
    )
    return Page.of([_read(r) for r in rows], params=page, total=total)


@router.post(
    "",
    response_model=RuleRead,
    status_code=201,
    dependencies=[require(rule_service.PERM_MANAGE)],
    summary="Create a rule. Type, scope and start date are fixed once created.",
)
async def create_rule(payload: RuleCreate, ctx: Access, uow: UowDep) -> RuleRead:
    row = await rule_service.create(
        uow.session,
        ctx,
        rule_service.RuleInput(
            rule_type=payload.rule_type,
            name=payload.name,
            description=payload.description,
            scope=payload.scope,
            condition=payload.condition,
            value=payload.value,
            priority=payload.priority,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
            is_active=payload.is_active,
        ),
    )
    await uow.session.refresh(row)
    return _read(row)


@router.post(
    "/resolve",
    response_model=ResolveResponse,
    dependencies=[require(rule_service.PERM_VIEW)],
    summary="Which rule applies to this context, and why each other rule does not",
)
async def resolve_rule(
    payload: ResolveRequest, ctx: Access, session: SessionDep
) -> ResolveResponse:
    at = payload.at or utcnow().date()
    winner, verdicts = await resolver.explain(
        session,
        company_id=ctx.company_id,
        rule_type=payload.rule_type,
        context=payload.context,
        at=at,
    )
    winner_row = await session.get(BusinessRule, winner.id) if winner else None
    considered = sorted(
        verdicts,
        key=lambda v: (not v.applies, -v.rule.specificity, -v.rule.priority),
    )
    return ResolveResponse(
        at=at,
        winner=_read(winner_row) if winner_row else None,
        considered=[
            VerdictRead(
                rule_id=v.rule.id,
                name=v.rule.name,
                scope=dict(v.rule.scope),
                priority=v.rule.priority,
                specificity=v.rule.specificity,
                applies=v.applies,
                reason=v.reason,
                winner=winner is not None and v.rule.id == winner.id,
            )
            for v in considered
        ],
    )


@router.get("/{rule_id}", response_model=RuleRead, dependencies=[require(rule_service.PERM_VIEW)])
async def get_rule(rule_id: UUID, ctx: Access, session: SessionDep) -> RuleRead:
    return _read(await rule_service.get(session, ctx, rule_id))


@router.patch(
    "/{rule_id}",
    response_model=RuleRead,
    dependencies=[require(rule_service.PERM_MANAGE)],
    summary="Change a rule's value, condition, priority, end date or active flag",
)
async def update_rule(
    rule_id: UUID,
    payload: RuleUpdate,
    ctx: Access,
    uow: UowDep,
    if_match: IfMatch = None,
) -> RuleRead:
    sent = payload.model_fields_set
    row = await rule_service.update(
        uow.session,
        ctx,
        rule_id,
        rule_service.RuleChange(
            name=payload.name,
            description=payload.description,
            value=payload.value,
            condition=payload.condition,
            condition_set="condition" in sent,
            priority=payload.priority,
            effective_to=payload.effective_to,
            effective_to_set="effective_to" in sent,
            is_active=payload.is_active,
        ),
        expected_version=if_match,
    )
    await uow.session.refresh(row)
    return _read(row)
