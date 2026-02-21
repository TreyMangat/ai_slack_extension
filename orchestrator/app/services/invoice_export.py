from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from typing import Iterable


INVOICE_EXPORT_COLUMNS = ["invoice_id", "customer", "status", "issued_on", "currency", "amount"]


SMOKE_INVOICES: list[dict[str, str | Decimal | date]] = [
    {
        "invoice_id": "INV-1001",
        "customer": "Acme Corp",
        "status": "paid",
        "issued_on": date(2026, 2, 1),
        "currency": "USD",
        "amount": Decimal("1250.00"),
    },
    {
        "invoice_id": "INV-1002",
        "customer": "Acme Corp",
        "status": "open",
        "issued_on": date(2026, 2, 3),
        "currency": "USD",
        "amount": Decimal("200.00"),
    },
    {
        "invoice_id": "INV-1003",
        "customer": "Globex",
        "status": "open",
        "issued_on": date(2026, 2, 4),
        "currency": "USD",
        "amount": Decimal("450.75"),
    },
    {
        "invoice_id": "INV-1004",
        "customer": "Initech",
        "status": "void",
        "issued_on": date(2026, 2, 6),
        "currency": "USD",
        "amount": Decimal("99.99"),
    },
]


def filter_invoices(
    invoices: Iterable[dict[str, str | Decimal | date]],
    *,
    customer: str | None = None,
    status: str | None = None,
    issued_from: date | None = None,
    issued_to: date | None = None,
) -> list[dict[str, str | Decimal | date]]:
    filtered: list[dict[str, str | Decimal | date]] = []

    customer_filter = (customer or "").strip().lower()
    status_filter = (status or "").strip().lower()

    for invoice in invoices:
        invoice_customer = str(invoice.get("customer", "")).lower()
        invoice_status = str(invoice.get("status", "")).lower()
        invoice_issued_on = invoice.get("issued_on")

        if customer_filter and customer_filter not in invoice_customer:
            continue
        if status_filter and status_filter != invoice_status:
            continue
        if issued_from and isinstance(invoice_issued_on, date) and invoice_issued_on < issued_from:
            continue
        if issued_to and isinstance(invoice_issued_on, date) and invoice_issued_on > issued_to:
            continue
        filtered.append(invoice)

    return filtered


def export_invoices_csv(invoices: Iterable[dict[str, str | Decimal | date]]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=INVOICE_EXPORT_COLUMNS)
    writer.writeheader()

    for invoice in invoices:
        writer.writerow(
            {
                "invoice_id": invoice.get("invoice_id", ""),
                "customer": invoice.get("customer", ""),
                "status": invoice.get("status", ""),
                "issued_on": invoice.get("issued_on", ""),
                "currency": invoice.get("currency", ""),
                "amount": invoice.get("amount", ""),
            }
        )

    return output.getvalue()
