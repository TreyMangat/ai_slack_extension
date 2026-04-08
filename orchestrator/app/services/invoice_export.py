from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from io import StringIO
from typing import Iterable


@dataclass(frozen=True)
class InvoiceFilter:
    status: str | None = None
    customer: str | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    issued_from: date | None = None
    issued_to: date | None = None


def _as_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        return date.fromisoformat(value)
    return None


def _matches(invoice: dict[str, object], filters: InvoiceFilter) -> bool:
    if filters.status and str(invoice.get("status", "")).strip().lower() != filters.status.strip().lower():
        return False

    if filters.customer and str(invoice.get("customer", "")).strip().lower() != filters.customer.strip().lower():
        return False

    amount = float(invoice.get("amount", 0) or 0)
    if filters.min_amount is not None and amount < filters.min_amount:
        return False
    if filters.max_amount is not None and amount > filters.max_amount:
        return False

    issued_at = _as_date(invoice.get("issued_at"))
    if filters.issued_from and (issued_at is None or issued_at < filters.issued_from):
        return False
    if filters.issued_to and (issued_at is None or issued_at > filters.issued_to):
        return False

    return True


def export_invoices_csv(
    invoices: Iterable[dict[str, object]],
    *,
    filters: InvoiceFilter | None = None,
) -> str:
    """Export invoices to CSV after applying optional filters."""

    active_filters = filters or InvoiceFilter()
    filtered = [invoice for invoice in invoices if _matches(invoice, active_filters)]

    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=["invoice_id", "customer", "amount", "status", "issued_at"])
    writer.writeheader()
    for invoice in filtered:
        writer.writerow(
            {
                "invoice_id": invoice.get("invoice_id", ""),
                "customer": invoice.get("customer", ""),
                "amount": invoice.get("amount", ""),
                "status": invoice.get("status", ""),
                "issued_at": invoice.get("issued_at", ""),
            }
        )

    return output.getvalue()
