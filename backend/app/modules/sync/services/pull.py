"""Answer a device's "what changed since I last asked" (docs/06 §4).

Every module that owns reference data a phone caches offers a feed of
`FeedRow`s ordered by `server_seq`. This merges them by that one global
sequence and cuts the page, so the cursor handed back is a true high-water mark
across every entity: nothing before it is missing, nothing after it was
skipped.

The device is not asked to say which of its cached rows it holds. It sends the
cursor; deleted and no-longer-relevant rows come back as tombstones.

**Known limit.** A sequence value is taken when a row is written, not when its
transaction commits. A slow transaction can therefore commit a row *behind* a
cursor another device already advanced past. Reference data changes rarely and
in short transactions, so this is rare, and the app closes the gap by doing a
full pull (`since=0`) on first launch and once a day.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.errors import ValidationError
from app.core.sync import FeedRow
from app.core.types import utcnow
from app.modules.identity.services import devices
from app.modules.masterdata.services import sync_feed as masterdata_feed
from app.modules.org.services import sync_feed as org_feed
from app.modules.procurement.services import sync_feed as procurement_feed
from app.modules.rules.services import sync_feed as rules_feed
from app.modules.sync.schemas import PullEntity, PullResponse
from app.modules.vendors.services import sync_feed as vendors_feed

# What a person needs in hand to record a delivery is available to anyone who
# may record one, even if their role has no general "view" right on the entity.
CAPTURE = "deliveries.create"

# entity -> permissions, any one of which lets the person sync it.
ENTITY_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "materials": ("materials.view", CAPTURE),
    "units": ("units.view", CAPTURE),
    "truck_types": ("materials.view", CAPTURE),
    "vendors": ("vendors.view", CAPTURE),
    "sites": (CAPTURE,),
    "rules": (CAPTURE,),
    "open_pos": (CAPTURE,),
}
ENTITIES = tuple(ENTITY_PERMISSIONS)

DEFAULT_LIMIT = 200
MAX_LIMIT = 500


async def _feed(
    session: AsyncSession,
    ctx: AccessContext,
    entity: str,
    *,
    since: int,
    limit: int,
    site_id: UUID | None,
) -> list[FeedRow]:
    company = ctx.company_id
    if entity in ("materials", "units", "truck_types"):
        return await masterdata_feed.changes(session, company, entity, since=since, limit=limit)
    if entity == "vendors":
        return await vendors_feed.changes(session, company, since=since, limit=limit)
    if entity == "sites":
        return await org_feed.changes(session, ctx, since=since, limit=limit, site_id=site_id)
    if entity == "rules":
        return await rules_feed.changes(session, company_id=company, since=since, limit=limit)
    return await procurement_feed.changes(session, ctx, since=since, limit=limit, site_id=site_id)


async def pull(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    entities: list[str],
    since: int,
    limit: int,
    site_id: UUID | None,
    device_id: str | None = None,
) -> PullResponse:
    unknown = [e for e in entities if e not in ENTITY_PERMISSIONS]
    if unknown:
        raise ValidationError(
            f"Unknown entity: {', '.join(unknown)}.",
            errors=[
                {
                    "field": "entities",
                    "code": "invalid",
                    "message": f"one of {', '.join(ENTITIES)}",
                }
            ],
        )
    limit = max(1, min(limit, MAX_LIMIT))
    wanted = list(dict.fromkeys(entities)) or list(ENTITIES)
    allowed = [e for e in wanted if ctx.has_any(*ENTITY_PERMISSIONS[e])]

    rows: list[FeedRow] = []
    for entity in allowed:
        # One extra row per feed, to know whether anything remains past the page.
        rows.extend(
            await _feed(session, ctx, entity, since=since, limit=limit + 1, site_id=site_id)
        )
    rows.sort(key=lambda r: r.seq)
    page = rows[:limit]

    if device_id:
        await devices.check_in(session, user_id=ctx.user_id, device_uid=device_id, create=False)

    return PullResponse(
        server_time=utcnow(),
        not_permitted=[e for e in wanted if e not in allowed],
        server_seq=page[-1].seq if page else since,
        has_more=len(rows) > limit,
        changes=[
            PullEntity(entity=r.entity, id=r.id, deleted=r.deleted, server_seq=r.seq, data=r.data)
            for r in page
        ],
    )
