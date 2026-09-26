"""The printed purchase order.

Pure: takes the already-assembled `PurchaseOrderRead` (so the document shows
exactly what the screen shows, with the same names and figures) and returns
HTML. Every value is escaped; nothing typed by a user can inject markup.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from html import escape

from app.modules.procurement.sourcing_schemas import PurchaseOrderRead

# A document only stands once it is approved. Anything earlier, or cancelled,
# carries a banner so a printed draft can never pass for the real thing.
_STANDING = {"APPROVED", "SENT", "ACKNOWLEDGED", "PARTIALLY_RECEIVED", "RECEIVED", "CLOSED"}

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
.terms { margin-top: 10pt; white-space: pre-wrap; }
.sign { display: flex; gap: 20pt; margin-top: 34pt; }
.sign div { flex: 1; border-top: 0.5pt solid #111; padding-top: 3pt; font-size: 8pt; }
"""


def _money(value: Decimal | None, currency: str) -> str:
    return "—" if value is None else f"{currency} {value:,.2f}"


def _qty(value: Decimal) -> str:
    text = f"{value:,.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _pct(value: Decimal | None) -> str:
    return "" if not value else f"{value:.2f}".rstrip("0").rstrip(".") + "%"


def _date(value: date | None) -> str:
    return "—" if value is None else value.strftime("%d %b %Y")


def _e(value: object | None) -> str:
    return "" if value is None else escape(str(value))


def render_purchase_order(po: PurchaseOrderRead, *, company_name: str) -> str:
    cur = po.currency_code
    banner = ""
    if po.status == "CANCELLED":
        banner = "CANCELLED"
    elif po.status not in _STANDING:
        banner = "NOT APPROVED — DRAFT COPY, NOT A VALID ORDER"

    rows = "".join(
        f"<tr><td>{i.line_no}</td>"
        f"<td><b>{_e(i.material_sku)}</b> {_e(i.material_name)}"
        + (f"<br><span class='muted'>{_e(i.description)}</span>" if i.description else "")
        + f"</td><td class='n'>{_qty(i.quantity)} {_e(i.unit_code)}</td>"
        f"<td class='n'>{_money(i.rate, cur)}</td>"
        f"<td class='n'>{_pct(i.discount_pct)}</td>"
        f"<td class='n'>{_pct(i.tax_pct)}</td>"
        f"<td class='n'>{_money(i.line_total, cur)}</td></tr>"
        for i in po.items
    )
    revision = f" &nbsp;<span class='muted'>revision {po.revision}</span>" if po.revision else ""
    project = _e(po.project_code) + (f" — {_e(po.project_name)}" if po.project_name else "")
    site = f"{_e(po.site_code)} — {_e(po.site_name)}" if po.site_code else "—"

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{_e(po.po_number)}</title><style>{_CSS}</style></head><body>
<div class="head">
  <div><h1>PURCHASE ORDER</h1><div class="muted">{_e(company_name)}</div></div>
  <div style="text-align:right"><b style="font-size:12pt">{_e(po.po_number)}</b>{revision}<br>
    Date: {_date(po.po_date)}<br>Expected delivery: {_date(po.expected_delivery_date)}</div>
</div>
{f'<div class="banner">{_e(banner)}</div>' if banner else ""}
<div class="parties">
  <div class="party"><h2>Vendor</h2><b>{_e(po.vendor_name)}</b><br>{_e(po.vendor_code)}
    <br>Payment terms: {_e(po.payment_terms) or "—"}</div>
  <div class="party"><h2>Deliver to</h2>{project}<br>Site: {site}
    <br>{_e(po.delivery_address) or ""}</div>
</div>
<table><thead><tr><th>#</th><th>Item</th><th class="n">Quantity</th><th class="n">Rate</th>
<th class="n">Disc.</th><th class="n">Tax</th><th class="n">Amount</th></tr></thead>
<tbody>{rows}</tbody></table>
<table class="totals">
  <tr><td>Subtotal</td><td class="n">{_money(po.subtotal, cur)}</td></tr>
  <tr><td>Discount</td><td class="n">- {_money(po.discount_amount, cur)}</td></tr>
  <tr><td>Tax</td><td class="n">{_money(po.tax_amount, cur)}</td></tr>
  <tr class="grand"><td>Total</td><td class="n">{_money(po.total_amount, cur)}</td></tr>
</table>
{
        f'<div class="terms"><b>Terms and conditions</b><br>{_e(po.terms_and_conditions)}</div>'
        if po.terms_and_conditions
        else ""
    }
{
        f'<p class="muted">Amendment {po.revision}: {_e(po.amendment_reason)}</p>'
        if po.revision and po.amendment_reason
        else ""
    }
<div class="sign"><div>Prepared by</div><div>Approved by{
        f" — {_date(po.approved_at.date())}" if po.approved_at else ""
    }</div>
<div>Vendor acknowledgement</div></div>
</body></html>"""
