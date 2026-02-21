from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class InvoiceRecord:
    invoice_id: str
    customer: str
    status: str
    issued_on: date
    amount: Decimal
    currency: str = "USD"


@dataclass(frozen=True)
class InvoiceExportFilters:
    statuses: tuple[str, ...] = ()
    customer_query: str = ""
    issued_from: date | None = None
    issued_to: date | None = None


def _matches_filters(invoice: InvoiceRecord, filters: InvoiceExportFilters) -> bool:
    if filters.statuses and invoice.status not in filters.statuses:
        return False

    if filters.customer_query and filters.customer_query.lower() not in invoice.customer.lower():
        return False

    if filters.issued_from and invoice.issued_on < filters.issued_from:
        return False

    if filters.issued_to and invoice.issued_on > filters.issued_to:
        return False

    return True


def export_invoices_csv(invoices: list[InvoiceRecord], filters: InvoiceExportFilters | None = None) -> str:
    """Export invoices as CSV, applying the provided filters first."""

    applied_filters = filters or InvoiceExportFilters()
    matching = [invoice for invoice in invoices if _matches_filters(invoice, applied_filters)]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["invoice_id", "customer", "status", "issued_on", "amount", "currency"])
    for invoice in matching:
        writer.writerow(
            [
                invoice.invoice_id,
                invoice.customer,
                invoice.status,
                invoice.issued_on.isoformat(),
                f"{invoice.amount:.2f}",
                invoice.currency,
            ]
        )
    return buffer.getvalue()
