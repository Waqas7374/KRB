"""HTML to PDF, shared by every printable document (purchase orders now; GRNs,
payslips and reports later).

WeasyPrint is synchronous and CPU-bound, so rendering runs in a worker thread
rather than blocking the event loop. It is also told never to fetch anything:
document content includes text people typed, and a rendered page must not be
able to make the server request an arbitrary URL.
"""

from __future__ import annotations

import asyncio
from typing import Any


def _refuse(url: str, *args: Any, **kwargs: Any) -> Any:
    raise ValueError(f"External resources are not loaded into PDFs: {url}")


def render_pdf(html: str) -> bytes:
    from weasyprint import HTML

    return bytes(HTML(string=html, url_fetcher=_refuse).write_pdf())


async def html_to_pdf(html: str) -> bytes:
    return await asyncio.to_thread(render_pdf, html)
