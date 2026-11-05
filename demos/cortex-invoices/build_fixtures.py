"""Build synthetic invoice packets and author-side references; never given to task agents.

Run with uv run --project demos/cortex-invoices python .../build_fixtures.py.
The printed line amounts are authoritative, including per-line rounding.
"""

import hashlib
import io
import json
import textwrap
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent
PACKETS = ("training", "trial-1", "trial-2", "trial-3")
VENDORS = (
    ("Alder Office Supply", "Beacon Workshop Ltd", "Cedar Field Services"),
    ("Delta Studio Supply", "Elm Fabrication Ltd", "Firth Survey Services"),
    ("Grove Office Supply", "Harbor Workshop Ltd", "Iris Field Services"),
    ("Juniper Studio Supply", "Kestrel Fabrication Ltd", "Larch Survey Services"),
)


def money(value):
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def item(description, quantity, unit_price, line_amount=None):
    amount = money(Decimal(quantity) * Decimal(unit_price)) if line_amount is None else line_amount
    return {"description": description, "quantity": quantity, "unit_price": unit_price, "line_amount": amount}


def catalog(index):
    n = index + 1
    return [
        {
            "invoice_id": f"00{n}071",
            "vendor": VENDORS[index][0],
            "layout": "text",
            "tax": str(Decimal("21.17") + index),
            "rows": [
                item(f"Archive cartons, reinforced - batch {n}", str(8 + index), "12.45"),
                item(f'Labels "Handle with care", roll {n}', "3", str(Decimal("9.80") + index)),
                item(f"Cut-to-length protective liner, grade {n}", "2.5", "14.76"),
                item(f"Returned labels credit, order {n}", "1", "-9.80"),
            ],
        },
        {
            "invoice_id": f"00{n}082",
            "vendor": VENDORS[index][1],
            "layout": "multipage",
            "tax": str(Decimal("153.28") + index),
            "rows": [
                item(f"Bench assembly, series {n}", "2", str(Decimal("1245.60") + 17 * index)),
                item(f"Alignment service, station {n}", "1.5", "86.40"),
                item(
                    f"Replacement seal kit for hydraulic assembly, series {n}, including mounting collars and protective sleeves; "
                    "compatible with the extended housing and supplied with inspection certificate",
                    "3",
                    str(Decimal("48.75") + index),
                ),
                item(f"Precision shims, pack {n}", "7", "4.375"),
                item(f"Freight and handling, zone {n}", "1", "42.50"),
                item(f"Volume discount, agreement {n}", "1", str(-Decimal("35.00") - index)),
            ],
        },
        {
            "invoice_id": f"00{n}093",
            "vendor": VENDORS[index][2],
            "layout": "hybrid",
            "tax": str(Decimal("89.64") + index),
            "discrepancy": "0.01" if index % 2 == 0 else "0.02",
            "rows": [
                item(f"Site survey, area {n}", "1", str(Decimal("425.50") + 23 * index)),
                item(f"Travel time, route {n}", "2.25", "64.80"),
                item(f"Calibration standard, set {n}", "2", "37.25"),
                item(f"Additional sensor mounts, revised scope {n}", "4", "18.95"),
                item(f"Cable assemblies, shielded, length {n} m", "5", "11.40"),
                item(f"Previously billed visit credit, ticket {n}", "1", str(-Decimal("25.00") - index)),
            ],
        },
    ]


def formatted(value):
    number = Decimal(value)
    digits = max(2, -number.as_tuple().exponent)
    text = f"{abs(number):,.{digits}f}"
    return f"({text})" if number < 0 else text


def pdf_page(invoice, number, rows, total=False, leading=None, split_last=False):
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=letter, invariant=True)
    page.setTitle(f"Invoice {invoice['invoice_id']}")
    page.setFont("Helvetica-Bold", 19)
    page.drawString(42, 748, invoice["vendor"])
    page.setFont("Helvetica", 11)
    page.drawString(42, 720, "INVOICE " + invoice["invoice_id"])
    page.drawRightString(570, 720, f"Page {number} of {1 if invoice['layout'] == 'text' else 2}")
    page.drawString(42, 701, "Bill to: Northwind Demonstration Company (synthetic fixture)")
    page.drawString(42, 683, "Currency: USD     Amounts in parentheses are credits.")
    page.setFont("Helvetica-Bold", 10)
    for x, label in ((42, "Description"), (385, "Qty"), (450, "Unit price"), (530, "Amount")):
        page.drawString(x, 649, label)
    page.line(42, 641, 570, 641)
    y = 620
    page.setFont("Helvetica", 10)
    if leading:
        for line in leading:
            page.drawString(42, y, line)
            y -= 14
        y -= 18
    remaining = None
    for index, row in enumerate(rows):
        lines = textwrap.wrap(row["description"], width=55, break_long_words=False, break_on_hyphens=False)
        if split_last and index == len(rows) - 1:
            lines, remaining = lines[:2], lines[2:]
        page.drawRightString(414, y, row["quantity"])
        page.drawRightString(503, y, formatted(row["unit_price"]))
        page.drawRightString(570, y, formatted(row["line_amount"]))
        for line in lines:
            page.drawString(42, y, line)
            y -= 14
        y -= 20
    if total:
        page.line(385, y, 570, y)
        y -= 25
        for label, amount in (("Subtotal", invoice["subtotal"]), ("Tax", invoice["tax"]), ("Total due", invoice["printed_total"])):
            page.drawString(385, y, label)
            page.drawRightString(570, y, formatted(amount))
            y -= 23
    else:
        page.drawString(42, 72, "Invoice continues on the next page. Totals are on the final page.")
    page.setFont("Helvetica", 8)
    page.drawString(42, 40, "SYNTHETIC DEMO INVOICE - no real customer or payment information")
    page.save()
    return PdfReader(buffer).pages[0], remaining


def scan_page(invoice):
    # An image-only PDF page with a clear table, no hidden text/OCR layer.
    scale = 3
    image = Image.new("RGB", (612 * scale, 792 * scale), "white")
    draw = ImageDraw.Draw(image)
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if not Path(font_path).exists():
        font_path = "/System/Library/Fonts/Supplemental/Arial.ttf"
    regular = ImageFont.truetype(font_path, 10 * scale)
    bold = ImageFont.truetype(font_path, 17 * scale)

    def text(x, y, content, font=regular, right=False):
        draw.text((x * scale, y * scale), content, fill="black", font=font, anchor="ra" if right else "la")

    text(42, 35, invoice["vendor"], bold)
    text(42, 67, "INVOICE " + invoice["invoice_id"] + " - Page 2 of 2")
    text(42, 90, "Continuation of line items. Currency: USD.")
    for x, label in ((42, "Description"), (378, "Qty"), (450, "Unit price"), (533, "Amount")):
        text(x, 128, label)
    draw.line((42 * scale, 150 * scale, 570 * scale, 150 * scale), fill="black", width=2)
    y = 168
    for row in invoice["rows"][3:]:
        text(412, y, row["quantity"], right=True)
        text(503, y, formatted(row["unit_price"]), right=True)
        text(570, y, formatted(row["line_amount"]), right=True)
        for line in textwrap.wrap(row["description"], width=52):
            text(42, y, line)
            y += 16
        y += 23
    y += 20
    for label, amount in (("Subtotal", invoice["subtotal"]), ("Tax", invoice["tax"]), ("Total due", invoice["printed_total"])):
        text(385, y, label)
        text(570, y, formatted(amount), right=True)
        y += 27
    text(42, 738, "SYNTHETIC DEMO INVOICE - image-only scanned continuation")
    buffer = io.BytesIO()
    image.save(buffer, "PDF", resolution=216)
    return PdfReader(buffer).pages[0]


def main():
    reference = {}
    manifest = {}
    for index, packet in enumerate(PACKETS):
        invoices = catalog(index)
        target = ROOT / "fixtures" / packet
        target.mkdir(parents=True, exist_ok=True)
        for invoice in invoices:
            subtotal = sum(Decimal(row["line_amount"]) for row in invoice["rows"])
            total = subtotal + Decimal(invoice["tax"])
            invoice["subtotal"] = money(subtotal)
            invoice["computed_total"] = money(total)
            invoice["printed_total"] = money(total + Decimal(invoice.get("discrepancy", "0")))
            invoice["difference"] = money(total - Decimal(invoice["printed_total"]))
            invoice["status"] = "matched" if invoice["difference"] == "0.00" else "discrepancy"
            writer = PdfWriter()
            if invoice["layout"] == "text":
                first, _ = pdf_page(invoice, 1, invoice["rows"], total=True)
                writer.add_page(first)
            else:
                first, remaining = pdf_page(invoice, 1, invoice["rows"][:3], split_last=invoice["layout"] == "multipage")
                writer.add_page(first)
                second = (
                    scan_page(invoice)
                    if invoice["layout"] == "hybrid"
                    else pdf_page(invoice, 2, invoice["rows"][3:], total=True, leading=remaining)[0]
                )
                writer.add_page(second)
            path = target / f"{invoice['invoice_id']}.pdf"
            writer.write(path)
            manifest[str(path.relative_to(ROOT))] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "pages": len(writer.pages)}
        reference[packet] = invoices
    oracle = ROOT / "oracle"
    oracle.mkdir(exist_ok=True)
    (oracle / "reference.json").write_text(json.dumps(reference, indent=2) + "\n")
    (ROOT / "fixture-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Built 12 synthetic PDFs, four independent evaluation packets, and author-side reference data.")


if __name__ == "__main__":
    main()
