from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.invoice_export import InvoiceExportFilters, InvoiceRecord, export_invoices_csv


def _sample_invoices() -> list[InvoiceRecord]:
    return [
        InvoiceRecord(
            invoice_id="INV-001",
            customer="Acme Corp",
            status="paid",
            issued_on=date(2025, 1, 3),
            amount=Decimal("1200.00"),
        ),
        InvoiceRecord(
            invoice_id="INV-002",
            customer="Acme Corp",
            status="open",
            issued_on=date(2025, 1, 8),
            amount=Decimal("320.50"),
        ),
        InvoiceRecord(
            invoice_id="INV-003",
            customer="Beta Labs",
            status="open",
            issued_on=date(2025, 2, 2),
            amount=Decimal("540.00"),
        ),
    ]


def test_export_invoices_csv_includes_all_without_filters() -> None:
    csv_data = export_invoices_csv(_sample_invoices())

    assert "invoice_id,customer,status,issued_on,amount,currency" in csv_data
    assert "INV-001,Acme Corp,paid,2025-01-03,1200.00,USD" in csv_data
    assert "INV-002,Acme Corp,open,2025-01-08,320.50,USD" in csv_data
    assert "INV-003,Beta Labs,open,2025-02-02,540.00,USD" in csv_data


def test_export_invoices_csv_respects_status_and_date_filters() -> None:
    filters = InvoiceExportFilters(
        statuses=("open",),
        issued_from=date(2025, 1, 10),
        issued_to=date(2025, 2, 10),
    )

    csv_data = export_invoices_csv(_sample_invoices(), filters=filters)

    assert "INV-001" not in csv_data
    assert "INV-002" not in csv_data
    assert "INV-003,Beta Labs,open,2025-02-02,540.00,USD" in csv_data


def test_export_invoices_csv_respects_customer_filter() -> None:
    filters = InvoiceExportFilters(customer_query="acme")

    csv_data = export_invoices_csv(_sample_invoices(), filters=filters)

    assert "INV-001,Acme Corp,paid,2025-01-03,1200.00,USD" in csv_data
    assert "INV-002,Acme Corp,open,2025-01-08,320.50,USD" in csv_data
    assert "INV-003" not in csv_data
