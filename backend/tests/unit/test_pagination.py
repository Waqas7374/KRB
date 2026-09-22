"""Pagination, sorting and the collection envelope.

These are pure functions over SQLAlchemy Core constructs, so no database is
needed: compiling the statement is enough to assert the ORDER BY it produces.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.errors import BadRequestError, ValidationError
from app.core.pagination import (
    MAX_LIMIT,
    MAX_OFFSET,
    Page,
    PageParams,
    apply_sort,
    decode_cursor,
    encode_cursor,
)


class _Base(DeclarativeBase):
    pass


class _Delivery(_Base):
    __tablename__ = "deliveries_for_test"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    truck_number: Mapped[str] = mapped_column(String)
    captured_at: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)


SORTABLE = {"truck_number", "captured_at", "created_at"}


def _order_by_sql(sort: str | None) -> str:
    stmt = apply_sort(select(_Delivery), _Delivery, sort, allowed=SORTABLE)
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


class TestPageParams:
    def test_defaults(self) -> None:
        params = PageParams()
        assert params.limit == 50
        assert params.offset == 0

    def test_limit_is_capped(self) -> None:
        with pytest.raises(PydanticValidationError):
            PageParams(limit=MAX_LIMIT + 1)

    def test_deep_offset_is_refused(self) -> None:
        """A deep OFFSET on a growing table is a slow scan waiting to happen."""
        with pytest.raises(PydanticValidationError):
            PageParams(offset=MAX_OFFSET + 1)

    def test_sort_field_must_look_like_a_field_name(self) -> None:
        with pytest.raises(PydanticValidationError):
            PageParams(sort="captured_at; DROP TABLE deliveries")

    def test_valid_sort_is_accepted(self) -> None:
        assert PageParams(sort="-captured_at,truck_number").sort == "-captured_at,truck_number"


class TestApplySort:
    def test_default_sort_is_applied_when_none_given(self) -> None:
        assert "created_at DESC" in _order_by_sql(None)

    def test_descending_prefix(self) -> None:
        assert "captured_at DESC" in _order_by_sql("-captured_at")

    def test_ascending_by_default(self) -> None:
        sql = _order_by_sql("truck_number")
        assert "truck_number ASC" in sql

    def test_multiple_fields_keep_their_order(self) -> None:
        sql = _order_by_sql("-captured_at,truck_number")
        assert sql.index("captured_at DESC") < sql.index("truck_number ASC")

    def test_a_stable_tiebreaker_is_always_appended(self) -> None:
        """Without it, rows repeat or vanish between pages."""
        assert _order_by_sql("truck_number").rstrip().endswith("id DESC")

    def test_unknown_field_is_rejected_rather_than_ignored(self) -> None:
        """Silently ignoring a sort is how someone exports the wrong data."""
        with pytest.raises(ValidationError) as exc_info:
            _order_by_sql("salary")
        assert exc_info.value.status_code == 422
        assert exc_info.value.errors[0]["field"] == "sort"


class TestCursor:
    def test_round_trip(self) -> None:
        payload = {"captured_at": "2026-09-21T08:55:03+00:00", "id": "018f-abc"}
        assert decode_cursor(encode_cursor(payload)) == payload

    def test_cursor_is_url_safe_and_unpadded(self) -> None:
        cursor = encode_cursor({"id": "x" * 40})
        assert "=" not in cursor
        assert "+" not in cursor and "/" not in cursor

    def test_malformed_cursor_is_a_bad_request(self) -> None:
        with pytest.raises(BadRequestError):
            decode_cursor("!!!not-base64!!!")


class TestPageEnvelope:
    def test_reports_has_more_from_the_total(self) -> None:
        page = Page[str].of(["a", "b"], params=PageParams(limit=2, offset=0), total=10)
        assert page.page.total == 10
        assert page.page.has_more is True

    def test_last_page_has_no_more(self) -> None:
        page = Page[str].of(["i", "j"], params=PageParams(limit=2, offset=8), total=10)
        assert page.page.has_more is False

    def test_cursor_mode_reports_has_more_from_the_cursor(self) -> None:
        page = Page[str].of(["a"], params=PageParams(limit=1), next_cursor="abc")
        assert page.page.total is None
        assert page.page.has_more is True
        assert page.page.next_cursor == "abc"

    def test_cursor_mode_without_a_next_cursor_is_the_end(self) -> None:
        assert Page[str].of(["a"], params=PageParams(limit=1)).page.has_more is False

    def test_extra_meta_is_carried_through(self) -> None:
        page = Page[str].of([], params=PageParams(), total=0, extra={"filters": {"status": "OPEN"}})
        assert page.meta == {"filters": {"status": "OPEN"}}
