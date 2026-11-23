from datetime import date

from sqlalchemy import select

from audit import log_audit
from extensions import db
from models import Invoice


INVOICE_TRANSITIONS = {
    "unpaid": frozenset({"paid", "overdue", "cancelled"}),
    "overdue": frozenset({"paid", "unpaid", "cancelled"}),
    "paid": frozenset(),
    "cancelled": frozenset(),
}


def refresh_overdue_invoices(today=None):
    today = today or date.today()
    overdue_ids = db.session.execute(
        select(Invoice.id).where(
            Invoice.status == "unpaid",
            Invoice.due_date.is_not(None),
            Invoice.due_date < today,
        )
    ).scalars().all()
    if not overdue_ids:
        return 0

    invoices = db.session.execute(
        select(Invoice).where(Invoice.id.in_(overdue_ids)).with_for_update()
    ).scalars()
    updated = 0
    for invoice in invoices:
        if invoice.status != "unpaid" or invoice.due_date is None or invoice.due_date >= today:
            continue
        invoice.status = "overdue"
        log_audit(
            "invoice.marked_overdue",
            "invoice",
            invoice.id,
            "Unpaid invoice was marked overdue after its due date.",
            actor_id=None,
            system=True,
        )
        updated += 1
    if updated:
        db.session.commit()
    return updated


def transition_invoice(invoice, status, *, actor_id=None):
    if status not in INVOICE_TRANSITIONS.get(invoice.status, frozenset()):
        return False
    if status == "overdue" and (invoice.due_date is None or invoice.due_date >= date.today()):
        return False
    old_status = invoice.status
    invoice.status = status
    log_audit(
        "invoice.status_changed",
        "invoice",
        invoice.id,
        f"Invoice status changed from {old_status} to {status}.",
        actor_id=actor_id,
    )
    return True
