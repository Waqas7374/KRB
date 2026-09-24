"""Guards on the request dependencies that integration tests cannot observe."""

from __future__ import annotations

from typing import get_args

from fastapi.params import Depends

from app.api.deps import UowDep


def test_unit_of_work_commits_before_the_response_is_sent() -> None:
    """With FastAPI's default dependency scope, a yield dependency's exit code
    (our commit) runs *after* the response has been sent, so a client can be
    told 200/201 for a transaction that has not committed yet, or never will.

    The in-process test client waits for the whole ASGI cycle, so no
    integration test can see that race; it was found in the browser, where a
    login followed immediately by /auth/me failed about one time in fifteen.
    This pins the fix.
    """
    depends = next(arg for arg in get_args(UowDep) if isinstance(arg, Depends))
    assert depends.scope == "function"
