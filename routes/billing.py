import secrets
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from audit import log_audit
from billing_service import INVOICE_TRANSITIONS, refresh_overdue_invoices, transition_invoice
from extensions import db
from models import DemoPaymentSimulation, Invoice
from utils.decorators import roles_required

billing_bp = Blueprint("billing", __name__, url_prefix="/billing")


@billing_bp.get("/")
@roles_required("admin", "patient")
def list_invoices():
    refresh_overdue_invoices()
    query = Invoice.query
    if current_user.normalized_role == "patient":
        query = query.filter_by(patient_id=current_user.id)
    invoices = query.order_by(Invoice.issued_at.desc()).all()
    status_options = {
        invoice.id: sorted(INVOICE_TRANSITIONS.get(invoice.status, ()))
        for invoice in invoices
    }
    return render_template("invoices.html", invoices=invoices, status_options=status_options)


@billing_bp.get("/<int:invoice_id>/print")
@roles_required("admin", "patient")
def print_invoice(invoice_id):
    invoice = db.get_or_404(Invoice, invoice_id)
    if current_user.normalized_role == "patient" and invoice.patient_id != current_user.id:
        abort(403)
    refresh_overdue_invoices()
    invoice = db.get_or_404(Invoice, invoice_id)
    return render_template("invoice_print.html", invoice=invoice)


@billing_bp.post("/<int:invoice_id>/paid")
@roles_required("admin")
def mark_paid(invoice_id):
    return update_invoice_status(invoice_id, "paid")


@billing_bp.post("/<int:invoice_id>/status")
@roles_required("admin")
def change_status(invoice_id):
    return update_invoice_status(invoice_id, request.form.get("status", ""))


@billing_bp.post("/<int:invoice_id>/simulate-payment")
@roles_required("patient")
def simulate_payment(invoice_id):
    invoice = db.get_or_404(Invoice, invoice_id)
    if invoice.patient_id != current_user.id:
        abort(403)
    refresh_overdue_invoices()
    invoice = db.get_or_404(Invoice, invoice_id)
    try:
        amount = Decimal(str(invoice.amount))
        valid_amount = (
            amount.is_finite()
            and amount > 0
            and amount <= Decimal("99999999.99")
            and amount == amount.quantize(Decimal("0.01"))
        )
    except (InvalidOperation, TypeError, ValueError):
        valid_amount = False
    if not valid_amount:
        abort(400, description="This invoice has an invalid amount and cannot be simulated.")
    if invoice.status not in {"unpaid", "overdue"}:
        abort(400, description="Only unpaid or overdue invoices can use the payment demonstration.")
    if not transition_invoice(invoice, "paid"):
        abort(400, description="This invoice cannot transition to Paid.")
    payment = DemoPaymentSimulation(
        invoice=invoice,
        patient_id=current_user.id,
        amount=amount,
        reference=f"DEMO-{secrets.token_hex(12).upper()}",
    )
    db.session.add(payment)
    try:
        db.session.flush()
        log_audit(
            "invoice.payment_simulated",
            "invoice",
            invoice.id,
            "Patient completed a demonstration payment simulation; no real payment was processed.",
        )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        invoice = db.get_or_404(Invoice, invoice_id)
        if invoice.status != "paid":
            raise
        flash("This invoice has already been marked paid.", "info")
        return redirect(url_for("billing.list_invoices"))
    flash(
        f"Demo payment recorded for {amount:,.2f} USD (reference {payment.reference}). "
        "No real payment was processed.",
        "success",
    )
    return redirect(url_for("billing.payment_confirmation", invoice_id=invoice_id))


@billing_bp.get("/<int:invoice_id>/payment-confirmation")
@roles_required("patient")
def payment_confirmation(invoice_id):
    payment = DemoPaymentSimulation.query.filter_by(
        invoice_id=invoice_id, patient_id=current_user.id
    ).first()
    if payment is None:
        abort(404)
    return render_template("payment_confirmation.html", payment=payment)


def update_invoice_status(invoice_id, status):
    invoice = db.get_or_404(Invoice, invoice_id)
    if status not in Invoice.STATUSES:
        abort(400, description="Choose a valid invoice status.")
    if invoice.status != status:
        if not transition_invoice(invoice, status):
            abort(
                400,
                description=f"An invoice cannot transition from {invoice.status.title()} to {status.title()}.",
            )
        db.session.commit()
    flash(f"Invoice marked as {status.title()}.", "success")
    return redirect(url_for("billing.list_invoices"))
