from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from audit import log_audit
from extensions import db
from models import Appointment, User
from utils.decorators import roles_required

appointments_bp = Blueprint("appointments", __name__, url_prefix="/appointments")

STATUS_TRANSITIONS = {
    "scheduled": frozenset({"confirmed", "rejected", "cancelled"}),
    "confirmed": frozenset({"completed", "cancelled"}),
    "rejected": frozenset(),
    "completed": frozenset(),
    "cancelled": frozenset(),
}
ROLE_TRANSITIONS = {
    "doctor": frozenset({"confirmed", "rejected", "completed"}),
    "patient": frozenset({"cancelled"}),
    "admin": frozenset({"confirmed", "rejected", "completed", "cancelled"}),
}


def available_status_transitions(appointment, role):
    return sorted(STATUS_TRANSITIONS.get(appointment.status, set()) & ROLE_TRANSITIONS.get(role, set()))


@appointments_bp.get("/")
@roles_required("admin", "doctor", "patient")
def list_appointments():
    role = current_user.normalized_role
    query = Appointment.query
    if role == "patient":
        query = query.filter_by(patient_id=current_user.id)
    elif role == "doctor":
        query = query.filter_by(doctor_id=current_user.id)
    appointments = query.order_by(Appointment.scheduled_at.desc()).all()
    status_options = {
        appointment.id: available_status_transitions(appointment, role)
        for appointment in appointments
    }
    return render_template(
        "appointments.html", appointments=appointments, status_options=status_options
    )


@appointments_bp.route("/new", methods=["GET", "POST"])
@roles_required("patient")
def create_appointment():
    doctors = User.query.filter(db.func.lower(db.func.trim(User.role)) == "doctor").order_by(User.name).all()
    if request.method == "POST":
        try:
            doctor_id = int(request.form.get("doctor_id", ""))
            scheduled_at = datetime.strptime(request.form.get("scheduled_at", ""), "%Y-%m-%dT%H:%M")
        except (ValueError, TypeError):
            flash("Choose a doctor and a valid appointment time.", "error")
            return render_template("appointment_form.html", doctors=doctors)
        doctor = db.session.get(User, doctor_id)
        reason = request.form.get("reason", "").strip()
        if not doctor or doctor.normalized_role != "doctor" or not reason or len(reason) > 500:
            flash("Enter a valid doctor and a short reason for your visit.", "error")
        elif scheduled_at <= datetime.now():
            flash("Appointment time must be in the future.", "error")
        elif Appointment.query.filter_by(doctor_id=doctor.id, scheduled_at=scheduled_at).first():
            flash("That doctor already has an appointment at this time. Please choose another time.", "error")
        else:
            appointment = Appointment(
                patient=current_user, doctor=doctor, scheduled_at=scheduled_at, reason=reason
            )
            db.session.add(appointment)
            try:
                db.session.flush()
                log_audit(
                    "appointment.booked",
                    "appointment",
                    appointment.id,
                    "Patient requested an appointment.",
                )
                db.session.commit()
                flash("Appointment request submitted. Its status is now Scheduled.", "success")
                return redirect(url_for("appointments.list_appointments"))
            except IntegrityError:
                db.session.rollback()
                flash("That doctor already has an appointment at this time. Please choose another time.", "error")
    return render_template("appointment_form.html", doctors=doctors)


@appointments_bp.post("/<int:appointment_id>/status")
@roles_required("admin", "doctor", "patient")
def update_status(appointment_id):
    appointment = db.get_or_404(Appointment, appointment_id)
    role = current_user.normalized_role
    if role == "doctor" and appointment.doctor_id != current_user.id:
        abort(403)
    if role == "patient" and appointment.patient_id != current_user.id:
        abort(403)
    status = request.form.get("status", "")
    if status not in Appointment.STATUSES:
        abort(400, description="Choose a valid appointment status.")
    if status not in available_status_transitions(appointment, role):
        abort(
            400,
            description=f"The appointment cannot transition from {appointment.status} to {status} for your role.",
        )
    old_status = appointment.status
    appointment.status = status
    log_audit(
        "appointment.status_changed",
        "appointment",
        appointment.id,
        f"Appointment status changed from {old_status} to {status}.",
    )
    db.session.commit()
    flash(f"Appointment {appointment.id} is now {status.title()}.", "success")
    return redirect(url_for("appointments.list_appointments"))
