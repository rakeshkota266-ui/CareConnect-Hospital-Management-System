
import secrets
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from audit import log_audit
from extensions import db
from models import Appointment, AuditEvent, DoctorProfile, Invoice, MedicalRecord, PatientProfile, User
from utils.decorators import roles_required
from validators import valid_email, valid_phone

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def parse_doctor_form():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    specialization = request.form.get("specialization", "").strip()
    qualification = request.form.get("qualification", "").strip()
    department = request.form.get("department", "").strip() or specialization
    phone = request.form.get("phone", "").strip()
    try:
        experience_years = int(request.form.get("experience_years", ""))
        consultation_fee = Decimal(request.form.get("consultation_fee", "").strip())
    except (ValueError, InvalidOperation):
        raise ValueError("Enter a whole number of experience years and a valid consultation fee.") from None

    try:
        valid_fee = (
            consultation_fee.is_finite()
            and Decimal("0") <= consultation_fee <= Decimal("99999999.99")
            and consultation_fee == consultation_fee.quantize(Decimal("0.01"))
        )
    except InvalidOperation:
        valid_fee = False

    if not name or len(name) > 120:
        raise ValueError("Enter a doctor name of 1–120 characters.")
    if not valid_email(email):
        raise ValueError("Enter a valid email address.")
    if not specialization or len(specialization) > 120:
        raise ValueError("Enter a specialization of 1–120 characters.")
    if not qualification or len(qualification) > 160:
        raise ValueError("Enter a qualification of 1–160 characters.")
    if not department or len(department) > 120 or not valid_phone(phone):
        raise ValueError("Check the department and phone number lengths.")
    if not 0 <= experience_years <= 80:
        raise ValueError("Experience must be a whole number from 0 to 80 years.")
    if not valid_fee:
        raise ValueError("Consultation fee must be from $0.00 to $99,999,999.99 with at most two decimal places.")

    return {
        "name": name,
        "email": email,
        "specialization": specialization,
        "qualification": qualification,
        "department": department,
        "phone": phone or None,
        "experience_years": experience_years,
        "consultation_fee": consultation_fee,
    }


@admin_bp.get("/")
@roles_required("admin")
def dashboard():
    patient_count = User.query.filter(db.func.lower(db.func.trim(User.role)) == "patient").count()
    doctor_count = User.query.filter(db.func.lower(db.func.trim(User.role)) == "doctor").count()
    appointment_count = Appointment.query.count()
    completed_count = Appointment.query.filter_by(status="completed").count()
    pending_count = Appointment.query.filter_by(status="scheduled").count()
    cancelled_count = Appointment.query.filter_by(status="cancelled").count()
    invoice_count = Invoice.query.count()
    unpaid_count = Invoice.query.filter_by(status="unpaid").count()
    stat_cards = [
        ("Total patients", patient_count, "♡", "mint"),
        ("Total doctors", doctor_count, "♙", "lilac"),
        ("Total appointments", appointment_count, "▦", "peach"),
        ("Completed appointments", completed_count, "✓", "blue"),
        ("Pending appointments", pending_count, "◷", "peach"),
        ("Cancelled appointments", cancelled_count, "×", "lilac"),
        ("Total invoices", invoice_count, "▧", "blue"),
        ("Unpaid invoices", unpaid_count, "$", "mint"),
    ]
    activities = []
    for appointment in Appointment.query.order_by(Appointment.created_at.desc()).limit(5):
        activities.append(
            {
                "kind": "Appointment",
                "description": f"{appointment.patient.name} · Dr. {appointment.doctor.name}",
                "when": appointment.created_at,
                "status": appointment.status,
                "url": url_for("appointments.list_appointments"),
            }
        )
    for record in MedicalRecord.query.order_by(MedicalRecord.recorded_at.desc()).limit(5):
        activities.append(
            {
                "kind": "Medical record",
                "description": f"Clinical notes added for {record.patient.name}",
                "when": record.recorded_at,
                "status": None,
                "url": url_for("records.detail", record_id=record.id),
            }
        )
    for invoice in Invoice.query.order_by(Invoice.issued_at.desc()).limit(5):
        activities.append(
            {
                "kind": "Invoice",
                "description": f"{invoice.patient.name} · ${invoice.amount:,.2f}",
                "when": invoice.issued_at,
                "status": invoice.status,
                "url": url_for("billing.list_invoices"),
            }
        )
    for user in User.query.order_by(User.created_at.desc()).limit(5):
        activities.append(
            {
                "kind": f"{user.normalized_role.title()} account",
                "description": user.name,
                "when": user.created_at,
                "status": None,
                "url": url_for("admin.users"),
            }
        )
    activities.sort(key=lambda activity: activity["when"], reverse=True)
    return render_template("admin/dashboard.html", stat_cards=stat_cards, activities=activities[:8])


@admin_bp.get("/audit")
@roles_required("admin")
def audit_log():
    events = AuditEvent.query.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc()).limit(200).all()
    return render_template("admin/audit.html", events=events)


@admin_bp.route("/doctors", methods=["GET", "POST"])
@roles_required("admin")
def doctors():
    if request.method == "POST":
        password = request.form.get("password", "")
        try:
            values = parse_doctor_form()
        except ValueError as error:
            flash(str(error), "error")
        else:
            if len(password) < 10:
                flash("Temporary passwords must contain at least 10 characters.", "error")
            elif db.session.query(User.id).filter(db.func.lower(User.email) == values["email"]).first():
                flash("An account with that email already exists.", "error")
            else:
                doctor = User(
                    name=values["name"],
                    email=values["email"],
                    role="doctor",
                    phone=values["phone"],
                )
                doctor.set_password(password)
                doctor.doctor_profile = DoctorProfile(
                    specialization=values["specialization"],
                    department=values["department"],
                    license_number=f"CC-{secrets.token_hex(12).upper()}",
                    qualification=values["qualification"],
                    experience_years=values["experience_years"],
                    consultation_fee=values["consultation_fee"],
                )
                db.session.add(doctor)
                try:
                    db.session.flush()
                    log_audit(
                        "admin.doctor_created",
                        "user",
                        doctor.id,
                        "Administrator added a doctor account.",
                    )
                    db.session.commit()
                    flash(f"Dr. {doctor.name} has been added to the care team.", "success")
                    return redirect(url_for("admin.doctors"))
                except IntegrityError:
                    db.session.rollback()
                    flash("That email is already in use. Review the doctor details and try again.", "error")

    return render_template(
        "admin/doctors.html",
        doctors=User.query.filter(db.func.lower(db.func.trim(User.role)) == "doctor").order_by(User.name).all(),
    )


@admin_bp.route("/doctors/<int:user_id>/edit", methods=["GET", "POST"])
@roles_required("admin")
def edit_doctor(user_id):
    doctor = db.get_or_404(User, user_id)
    if doctor.normalized_role != "doctor" or doctor.doctor_profile is None:
        abort(404)

    if request.method == "POST":
        try:
            values = parse_doctor_form()
        except ValueError as error:
            flash(str(error), "error")
        else:
            duplicate_email = User.query.filter(
                db.func.lower(User.email) == values["email"], User.id != doctor.id
            ).first()
            if duplicate_email:
                flash("An account with that email already exists.", "error")
            else:
                doctor.name = values["name"]
                doctor.email = values["email"]
                doctor.phone = values["phone"]
                profile = doctor.doctor_profile
                profile.specialization = values["specialization"]
                profile.qualification = values["qualification"]
                profile.department = values["department"]
                profile.experience_years = values["experience_years"]
                profile.consultation_fee = values["consultation_fee"]
                try:
                    log_audit(
                        "admin.doctor_updated",
                        "user",
                        doctor.id,
                        "Administrator updated a doctor profile.",
                    )
                    db.session.commit()
                    flash(f"Dr. {doctor.name}'s profile has been updated.", "success")
                    return redirect(url_for("admin.doctors"))
                except IntegrityError:
                    db.session.rollback()
                    flash("Those details could not be saved because an email is already in use.", "error")

    return render_template("admin/doctor_edit.html", doctor=doctor)


@admin_bp.route("/users", methods=["GET", "POST"])
@roles_required("admin")
def users():
    if request.method == "POST":
        role = request.form.get("role", "")
        password = request.form.get("password", "")
        if role not in {"patient", "doctor", "admin"}:
            flash("Choose a valid account role.", "error")
        elif len(password) < 10:
            flash("Temporary passwords must contain at least 10 characters.", "error")
        elif role == "doctor":
            try:
                values = parse_doctor_form()
            except ValueError as error:
                flash(str(error), "error")
            else:
                license_number = request.form.get("license_number", "").strip()
                if not license_number or len(license_number) > 80:
                    flash("Enter a doctor license number of 1–80 characters.", "error")
                elif db.session.query(User.id).filter(db.func.lower(User.email) == values["email"]).first():
                    flash("An account with that email already exists.", "error")
                elif DoctorProfile.query.filter_by(license_number=license_number).first():
                    flash("That doctor license number is already in use.", "error")
                else:
                    user = User(
                        name=values["name"],
                        email=values["email"],
                        role="doctor",
                        phone=values["phone"],
                    )
                    user.set_password(password)
                    user.doctor_profile = DoctorProfile(
                        license_number=license_number,
                        specialization=values["specialization"],
                        department=values["department"],
                        qualification=values["qualification"],
                        experience_years=values["experience_years"],
                        consultation_fee=values["consultation_fee"],
                    )
                    db.session.add(user)
                    try:
                        db.session.flush()
                        log_audit(
                            "admin.account_created",
                            "user",
                            user.id,
                            f"Administrator created a {role} account.",
                        )
                        db.session.commit()
                        flash("Doctor account created with the submitted profile values.", "success")
                        return redirect(url_for("admin.users"))
                    except IntegrityError:
                        db.session.rollback()
                        flash("That email or doctor license number is already in use.", "error")
        else:
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            phone = request.form.get("phone", "").strip()
            if (
                not name
                or len(name) > 120
                or any(ord(char) < 32 for char in name)
                or not valid_email(email)
                or not valid_phone(phone)
            ):
                flash("Enter a valid name, email, and phone number.", "error")
            elif db.session.query(User.id).filter(db.func.lower(User.email) == email).first():
                flash("An account with that email already exists.", "error")
            else:
                user = User(name=name, email=email, role=role, phone=phone or None)
                user.set_password(password)
                if role == "patient":
                    user.patient_profile = PatientProfile()
                db.session.add(user)
                try:
                    db.session.flush()
                    log_audit(
                        "admin.account_created",
                        "user",
                        user.id,
                        f"Administrator created a {role} account.",
                    )
                    db.session.commit()
                    flash(f"{role.title()} account created.", "success")
                    return redirect(url_for("admin.users"))
                except IntegrityError:
                    db.session.rollback()
                    flash("That email is already in use.", "error")
    return render_template("admin/users.html", users=User.query.order_by(User.name).all())


@admin_bp.post("/users/<int:user_id>/role")
@roles_required("admin")
def update_role(user_id):
    user = db.get_or_404(User, user_id)
    role = request.form.get("role", "")
    if role not in {"patient", "doctor", "admin"}:
        abort(400, description="Choose a valid user role.")
    if role == user.normalized_role:
        return redirect(url_for("admin.users"))
    old_role = user.normalized_role
    admin_count = User.query.filter(db.func.lower(db.func.trim(User.role)) == "admin").count()
    if user.normalized_role == "admin" and role != "admin" and admin_count <= 1:
        abort(400, description="The last administrator account cannot be demoted.")
    if role == "doctor":
        abort(400, description="Create a doctor account with a license and department instead.")
    if user.normalized_role == "doctor":
        abort(400, description="Doctor accounts cannot be converted because their clinical history must be preserved.")
    user.role = role
    if role == "patient" and not user.patient_profile:
        user.patient_profile = PatientProfile()
    log_audit(
        "admin.role_changed",
        "user",
        user.id,
        f"Administrator changed an account role from {old_role} to {role}.",
    )
    db.session.commit()
    flash("User role updated.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/invoices/new", methods=["GET", "POST"])
@roles_required("admin")
def create_invoice():
    from billing_service import refresh_overdue_invoices

    refresh_overdue_invoices()
    patients = User.query.filter(db.func.lower(db.func.trim(User.role)) == "patient").order_by(User.name).all()
    appointments = (
        Appointment.query.filter(~Appointment.invoices.any())
        .order_by(Appointment.scheduled_at.desc())
        .all()
    )
    if request.method == "POST":
        try:
            appointment_id = int(request.form["appointment_id"]) if request.form.get("appointment_id") else None
            patient_id = int(request.form["patient_id"]) if request.form.get("patient_id") else None
            amount = (
                Decimal(request.form.get("amount", "").strip())
                if appointment_id is None
                else None
            )
            due_date_value = request.form.get("due_date", "").strip()
            due_date = date.fromisoformat(due_date_value) if due_date_value else None
        except (KeyError, ValueError, InvalidOperation):
            flash("Check the patient, amount, and invoice due date.", "error")
            return render_template("admin/invoice_form.html", patients=patients, appointments=appointments)
        description = request.form.get("description", "").strip()
        appointment = db.session.get(Appointment, appointment_id) if appointment_id else None
        patient = appointment.patient if appointment else (
            db.session.get(User, patient_id) if patient_id else None
        )
        if appointment_id is not None and not appointment:
            flash("Choose a valid appointment.", "error")
            return render_template("admin/invoice_form.html", patients=patients, appointments=appointments)
        if appointment:
            doctor_profile = appointment.doctor.doctor_profile
            try:
                amount = Decimal(str(doctor_profile.consultation_fee)) if doctor_profile else None
                has_valid_fee = amount is not None and amount.is_finite() and amount > 0
            except (InvalidOperation, TypeError, ValueError):
                amount = None
                has_valid_fee = False
            if not has_valid_fee:
                flash("The appointment's doctor has no consultation fee configured.", "error")
                return render_template("admin/invoice_form.html", patients=patients, appointments=appointments)
            description = f"Consultation with Dr. {appointment.doctor.name}"
        try:
            valid_amount = (
                amount is not None
                and amount.is_finite()
                and Decimal("0.01") <= amount <= Decimal("99999999.99")
                and amount == amount.quantize(Decimal("0.01"))
            )
        except InvalidOperation:
            valid_amount = False
        if (
            not patient
            or patient.normalized_role != "patient"
            or not description
            or len(description) > 300
            or not valid_amount
        ):
            flash("Provide a valid patient, description, and positive USD amount with up to two decimals.", "error")
        elif appointment and Invoice.query.filter_by(appointment_id=appointment.id).first():
            flash("An invoice already exists for that appointment.", "error")
        else:
            invoice = Invoice(
                patient=patient,
                appointment=appointment,
                description=description,
                amount=amount,
                due_date=due_date,
            )
            db.session.add(invoice)
            try:
                db.session.flush()
                log_audit(
                    "admin.invoice_created",
                    "invoice",
                    invoice.id,
                    "Administrator created an appointment invoice."
                    if appointment
                    else "Administrator created a manual invoice.",
                )
                db.session.commit()
                refresh_overdue_invoices()
                flash("Invoice created.", "success")
                return redirect(url_for("billing.list_invoices"))
            except IntegrityError:
                db.session.rollback()
                flash("An invoice already exists for that appointment.", "error")
    return render_template("admin/invoice_form.html", patients=patients, appointments=appointments)
