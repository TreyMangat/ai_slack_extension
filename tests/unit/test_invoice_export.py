from __future__ import annotations

import csv
import io

from app.services.invoice_export import InvoiceExportFilters, export_invoices_csv


def _rows(csv_text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(csv_text)))


def test_export_invoices_csv_exports_all_rows_without_filters() -> None:
    invoices = [
        {"invoice_id": "INV-001", "customer": "Acme", "amount": 100, "status": "paid"},
        {"invoice_id": "INV-002", "customer": "Beta", "amount": 50, "status": "open"},
    ]

    exported = export_invoices_csv(invoices)

    assert _rows(exported) == [
        {"invoice_id": "INV-001", "customer": "Acme", "amount": "100", "status": "paid"},
        {"invoice_id": "INV-002", "customer": "Beta", "amount": "50", "status": "open"},
    ]


def test_export_invoices_csv_respects_status_and_customer_filters() -> None:
    invoices = [
        {"invoice_id": "INV-001", "customer": "Acme", "amount": 100, "status": "paid"},
        {"invoice_id": "INV-002", "customer": "Acme", "amount": 150, "status": "open"},
        {"invoice_id": "INV-003", "customer": "Beta", "amount": 75, "status": "paid"},
    ]

    exported = export_invoices_csv(
        invoices,
        InvoiceExportFilters(status="paid", customer="Acme"),
    )

    assert _rows(exported) == [
        {"invoice_id": "INV-001", "customer": "Acme", "amount": "100", "status": "paid"},
    ]
