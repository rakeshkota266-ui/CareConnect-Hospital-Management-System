from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from extensions import db
from models import Appointment, Invoice, MedicalRecord, User
from utils.decorators import roles_required
from validators import valid_phone

main_bp = Blueprint("main", __name__)


@main_bp.get("/")
def index():
    if current_user.is_authenticated:
        from flask import redirect, url_for

        return redirect(url_for("main.dashboard"))
    return render_template("home.html")


@main_bp.get("/dashboard")
@login_required
def dashboard():
    role = current_user.normalized_role
    if role == "admin":
        stat_cards = [
            ("Patients", User.query.filter(db.func.lower(db.func.trim(User.role)) == "patient").count(), "♡", "mint"),
            ("Care team", User.query.filter(db.func.lower(db.func.trim(User.role)) == "doctor").count(), "♙", "lilac"),
            ("Appointments", Appointment.query.count(), "▦", "peach"),
            ("Unpaid invoices", Invoice.query.filter_by(status="unpaid").count(), "▧", "blue"),
        ]
        appointments = Appointment.query.order_by(Appointment.scheduled_at.desc()).limit(6).all()
    elif role == "doctor":
        doctor_appointments = Appointment.query.filter_by(doctor_id=current_user.id)
        stat_cards = [
            ("Appointments", doctor_appointments.count(), "▦", "peach"),
            ("Patients", doctor_appointments.with_entities(Appointment.patient_id).distinct().count(), "♡", "mint"),
            ("Records written", MedicalRecord.query.filter_by(doctor_id=current_user.id).count(), "▤", "lilac"),
            ("Care role", "Doctor", "♙", "blue"),
        ]
        appointments = Appointment.query.filter_by(doctor_id=current_user.id).order_by(
            Appointment.scheduled_at.desc()
        ).limit(6).all()
    else:
        stat_cards = [
            ("Appointments", Appointment.query.filter_by(patient_id=current_user.id).count(), "▦", "peach"),
            ("Medical records", MedicalRecord.query.filter_by(patient_id=current_user.id).count(), "▤", "lilac"),
            ("Unpaid invoices", Invoice.query.filter_by(patient_id=current_user.id, status="unpaid").count(), "▧", "blue"),
            ("Care team", User.query.filter(db.func.lower(db.func.trim(User.role)) == "doctor").count(), "♙", "mint"),
        ]
        appointments = Appointment.query.filter_by(patient_id=current_user.id).order_by(
            Appointment.scheduled_at.desc()
        ).limit(6).all()
    return render_template("dashboard.html", stat_cards=stat_cards, appointments=appointments)


@main_bp.get("/doctors")
@login_required
def doctors():
    doctors = User.query.filter(db.func.lower(db.func.trim(User.role)) == "doctor").order_by(User.name).all()
    return render_template("doctors.html", doctors=doctors)


@main_bp.get("/patients")
@roles_required("admin")
def patients():
    patients = User.query.filter(db.func.lower(db.func.trim(User.role)) == "patient").order_by(User.name).all()
    return render_template("patients.html", patients=patients)


@main_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    patient_profile = current_user.patient_profile
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        password_fields_present = any((current_password, new_password, confirm_password))
        password_error = None
        if password_fields_present:
            if not current_password or not current_user.check_password(current_password):
                password_error = "Enter your current password correctly."
            elif len(new_password) < 10:
                password_error = "Choose a new password with at least 10 characters."
            elif new_password != confirm_password:
                password_error = "The new passwords do not match."
        if not name or len(name) > 120 or any(ord(char) < 32 for char in name) or not valid_phone(phone):
            flash("Enter a name and a valid phone number.", "error")
        elif password_error:
            flash(password_error, "error")
        else:
            if patient_profile:
                birth_date = request.form.get("date_of_birth", "").strip()
                try:
                    parsed_birth_date = date.fromisoformat(birth_date) if birth_date else None
                except ValueError:
                    flash("Enter a valid date of birth.", "error")
                    return render_template("profile.html")
                if parsed_birth_date and parsed_birth_date > date.today():
                    flash("Date of birth cannot be in the future.", "error")
                    return render_template("profile.html")
                if len(request.form.get("address", "").strip()) > 300 or len(
                    request.form.get("emergency_contact", "").strip()
                ) > 120:
                    flash("Check the length of your address and emergency contact.", "error")
                    return render_template("profile.html")
                gender = request.form.get("gender", "").strip()
                blood_group = request.form.get("blood_group", "").strip()
                if gender not in {"", "Woman", "Man", "Non-binary", "Other"}:
                    flash("Choose a valid gender option.", "error")
                    return render_template("profile.html")
                if blood_group not in {"", "A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"}:
                    flash("Choose a valid blood group.", "error")
                    return render_template("profile.html")
            elif current_user.doctor_profile:
                availability = request.form.get("availability", "").strip()
                if len(availability) > 200:
                    flash("Availability must be no more than 200 characters.", "error")
                    return render_template("profile.html")
            current_user.name = name
            current_user.phone = phone or None
            if patient_profile:
                patient_profile.date_of_birth = parsed_birth_date
                patient_profile.gender = gender or None
                patient_profile.blood_group = blood_group or None
                patient_profile.address = request.form.get("address", "").strip() or None
                patient_profile.emergency_contact = request.form.get("emergency_contact", "").strip() or None
            elif current_user.doctor_profile:
                current_user.doctor_profile.availability = availability or None
            if password_fields_present:
                current_user.set_password(new_password)
            db.session.commit()
            if password_fields_present:
                flash("Your profile and password have been updated.", "success")
            else:
                flash("Your profile has been updated.", "success")
            return redirect(url_for("main.profile"))
    return render_template("profile.html")
