"""The printed goods received note.

Pure: takes the already-assembled `GrnRead` (so the document shows exactly what
the screen shows, with the same names and figures — and, because that read model
withholds valuation from people who may not see it, a printout can never leak
what the screen would not) and returns HTML. Every value is escaped; nothing
typed by a user can inject markup.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from html import escape

from app.modules.grn.schemas import GrnRead

_CSS = """
@page { size: A4; margin: 18mm 15mm 20mm 15mm;
  @bottom-center { content: counter(page) " / " counter(pages); font-size: 8pt; color: #666; } }
body { font-family: "DejaVu Sans", sans-serif; font-size: 9pt; color: #111; }
h1 { font-size: 16pt; margin: 0; letter-spacing: 0.04em; }
.muted { color: #555; }
.head { display: flex; justify-content: space-between; align-items: flex-start; }
.banner { border: 1.5pt solid #b00020; color: #b00020; font-weight: bold; text-align: center;
  padding: 4pt; margin: 8pt 0; letter-spacing: 0.08em; }
.parties { display: flex; gap: 12pt; margin: 10pt 0; }
.party { flex: 1; border: 0.5pt solid #999; padding: 6pt 8pt; }
.party h2 { font-size: 8pt; text-transform: uppercase; margin: 0 0 3pt; color: #555; }
table { width: 100%; border-collapse: collapse; margin-top: 6pt; }
th { background: #eee; text-align: left; font-size: 8pt; padding: 4pt; border: 0.5pt solid #999; }
td { padding: 4pt; border: 0.5pt solid #ccc; vertical-align: top; }
.n { text-align: right; white-space: nowrap; }
.totals { width: 48%; margin-left: auto; }
.totals td { border: none; padding: 2pt 4pt; }
.totals .grand td { border-top: 1pt solid #111; font-weight: bold; font-size: 10pt; }
.warn { color: #8a5a00; }
.sign { display: flex; gap: 20pt; margin-top: 34pt; }
.sign div { flex: 1; border-top: 0.5pt solid #111; padding-top: 3pt; font-size: 8pt; }
"""

# Only a posted note has moved stock; anything else must not pass for the record of it.
_BANNERS = {
    "DRAFT": "DRAFT — NO STOCK HAS MOVED",
    "CANCELLED": "CANCELLED",
}


def _qty(value: Decimal | None) -> str:
    if value is None:
        return "—"
    text = f"{value:,.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _money(value: Decimal | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def _date(value: date | None) -> str:
    return "—" if value is None else value.strftime("%d %b %Y")


def _e(value: object | None) -> str:
    return "" if value is None else escape(str(value))


def render_grn(grn: GrnRead, *, company_name: str) -> str:
    banner = _BANNERS.get(grn.status, "")
    show_prices = not grn.prices_hidden

    price_head = '<th class="n">Rate</th><th class="n">Amount</th>' if show_prices else ""
    rows = "".join(
        f"<tr><td>{i.line_no}</td>"
        f"<td><b>{_e(i.material_sku)}</b> {_e(i.material_name)}"
        + (f"<br><span class='muted'>Batch {_e(i.batch_no)}</span>" if i.batch_no else "")
        + (
            f"<br><span class='warn'>Rejected: {_e(i.rejection_reason)}</span>"
            if i.rejection_reason
            else ""
        )
        + f"</td><td class='n'>{_qty(i.ordered_quantity)}</td>"
        f"<td class='n'>{_qty(i.delivered_quantity)} {_e(i.unit_code)}</td>"
        f"<td class='n'>{_qty(i.accepted_quantity)}</td>"
        f"<td class='n'>{_qty(i.rejected_quantity) if i.rejected_quantity else '—'}</td>"
        + (
            f"<td class='n'>{_money(i.rate)}</td><td class='n'>{_money(i.amount)}</td>"
            if show_prices
            else ""
        )
        + "</tr>"
        for i in grn.items
    )
    totals = (
        f"""<table class="totals">
  <tr><td>Value received</td><td class="n">{_money(grn.gross_amount)}</td></tr>
  <tr class="grand"><td>Net</td><td class="n">{_money(grn.net_amount)}</td></tr>
</table>"""
        if show_prices
        else ""
    )
    source = (
        f"Delivery {_e(grn.delivery_number)}"
        if grn.delivery_number
        else f"Counter purchase, bill {_e(grn.counter_reference)}"
    )
    order = f"<br>Order: {_e(grn.purchase_order_number)}" if grn.purchase_order_number else ""
    posted = f"<br>Posted: {_date(grn.posted_at.date())}" if grn.posted_at else ""

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{_e(grn.grn_number)}</title><style>{_CSS}</style></head><body>
<div class="head">
  <div><h1>GOODS RECEIVED NOTE</h1><div class="muted">{_e(company_name)}</div></div>
  <div style="text-align:right"><b style="font-size:12pt">{_e(grn.grn_number)}</b><br>
    Received: {_date(grn.received_date)}{posted}</div>
</div>
{f'<div class="banner">{_e(banner)}</div>' if banner else ""}
<div class="parties">
  <div class="party"><h2>From</h2><b>{_e(grn.vendor_name) or "—"}</b><br>{source}{order}</div>
  <div class="party"><h2>Received at</h2>Site: {_e(grn.site_code) or "—"}
    <br>Store: {_e(grn.warehouse_code)} — {_e(grn.warehouse_name)}
    <br>Inspection: {_e(grn.inspection_result.title())}</div>
</div>
<table><thead><tr><th>#</th><th>Item</th><th class="n">Ordered</th><th class="n">Delivered</th>
<th class="n">Accepted</th><th class="n">Rejected</th>{price_head}</tr></thead>
<tbody>{rows}</tbody></table>
{totals}
{f'<p class="muted">Remarks: {_e(grn.remarks)}</p>' if grn.remarks else ""}
{
        f'<p class="warn">Cancelled: {_e(grn.cancel_reason)}</p>'
        if grn.status == "CANCELLED" and grn.cancel_reason
        else ""
    }
<div class="sign"><div>Received by</div><div>Inspected by</div><div>Store keeper</div></div>
</body></html>"""
