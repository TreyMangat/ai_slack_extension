from __future__ import annotations

from datetime import date

from app.services.invoice_export import InvoiceFilter, export_invoices_csv


def _sample_invoices() -> list[dict[str, object]]:
    return [
        {
            "invoice_id": "INV-001",
            "customer": "Acme",
            "amount": 120.5,
            "status": "paid",
            "issued_at": "2026-01-02",
        },
        {
            "invoice_id": "INV-002",
            "customer": "Beta",
            "amount": 78.0,
            "status": "open",
            "issued_at": "2026-01-03",
        },
        {
            "invoice_id": "INV-003",
            "customer": "Acme",
            "amount": 450.0,
            "status": "paid",
            "issued_at": "2026-01-05",
        },
    ]


def test_export_invoices_csv_includes_header_and_rows() -> None:
    csv_text = export_invoices_csv(_sample_invoices())

    assert "invoice_id,customer,amount,status,issued_at" in csv_text
    assert "INV-001,Acme,120.5,paid,2026-01-02" in csv_text
    assert "INV-002,Beta,78.0,open,2026-01-03" in csv_text


def test_export_invoices_csv_respects_status_and_customer_filters() -> None:
    csv_text = export_invoices_csv(
        _sample_invoices(),
        filters=InvoiceFilter(status="paid", customer="acme"),
    )

    assert "INV-001,Acme,120.5,paid,2026-01-02" in csv_text
    assert "INV-003,Acme,450.0,paid,2026-01-05" in csv_text
    assert "INV-002" not in csv_text


def test_export_invoices_csv_respects_amount_and_date_filters() -> None:
    csv_text = export_invoices_csv(
        _sample_invoices(),
        filters=InvoiceFilter(min_amount=100, issued_from=date(2026, 1, 4)),
    )

    assert "INV-003,Acme,450.0,paid,2026-01-05" in csv_text
    assert "INV-001" not in csv_text
    assert "INV-002" not in csv_text
