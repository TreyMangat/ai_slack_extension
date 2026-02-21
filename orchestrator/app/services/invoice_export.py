from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class InvoiceExportFilters:
    status: str | None = None
    customer: str | None = None


def export_invoices_csv(invoices: Iterable[dict], filters: InvoiceExportFilters | None = None) -> str:
    """Export invoices as CSV while applying optional filters."""

    active_filters = filters or InvoiceExportFilters()

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["invoice_id", "customer", "amount", "status"])
    writer.writeheader()

    for raw in invoices:
        invoice_id = str(raw.get("invoice_id", "")).strip()
        customer = str(raw.get("customer", "")).strip()
        status = str(raw.get("status", "")).strip()

        if active_filters.status and status != active_filters.status:
            continue
        if active_filters.customer and customer != active_filters.customer:
            continue

        writer.writerow(
            {
                "invoice_id": invoice_id,
                "customer": customer,
                "amount": raw.get("amount", ""),
                "status": status,
            }
        )

    return output.getvalue()
