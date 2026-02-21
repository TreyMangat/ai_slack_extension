from __future__ import annotations

import csv
import io
from datetime import date

from app.services.invoice_export import SMOKE_INVOICES, export_invoices_csv, filter_invoices


def test_export_invoices_csv_includes_header_and_rows() -> None:
    csv_content = export_invoices_csv(SMOKE_INVOICES[:2])

    reader = csv.DictReader(io.StringIO(csv_content))
    rows = list(reader)

    assert reader.fieldnames == ["invoice_id", "customer", "status", "issued_on", "currency", "amount"]
    assert len(rows) == 2
    assert rows[0]["invoice_id"] == "INV-1001"


def test_filter_invoices_respects_status_and_customer() -> None:
    filtered = filter_invoices(SMOKE_INVOICES, status="open", customer="acme")

    assert [row["invoice_id"] for row in filtered] == ["INV-1002"]


def test_filter_invoices_respects_issued_date_range() -> None:
    filtered = filter_invoices(
        SMOKE_INVOICES,
        issued_from=date(2026, 2, 3),
        issued_to=date(2026, 2, 4),
    )

    assert [row["invoice_id"] for row in filtered] == ["INV-1002", "INV-1003"]
