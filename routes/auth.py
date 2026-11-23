from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy.exc import IntegrityError
from urllib.parse import urlsplit

from extensions import db
from models import PatientProfile, User
from validators import valid_email, valid_phone

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        phone = request.form.get("phone", "").strip()
        if not name or len(name) > 120 or any(ord(char) < 32 for char in name):
            flash("Enter your name using 1–120 printable characters.", "error")
        elif not valid_email(email):
            flash("Enter a valid email address, such as you@example.com.", "error")
        elif not valid_phone(phone):
            flash("Enter a valid phone number with at least 7 digits, or leave it blank.", "error")
        elif len(password) < 10:
            flash("Choose a password with at least 10 characters.", "error")
        elif password != confirm:
            flash("The passwords do not match.", "error")
        elif db.session.query(User.id).filter(db.func.lower(User.email) == email).first():
            flash("An account with that email already exists.", "error")
        else:
            user = User(name=name, email=email, phone=phone or None, role="patient")
            user.set_password(password)
            user.patient_profile = PatientProfile()
            db.session.add(user)
            try:
                db.session.commit()
                session.pop("_csrf_token", None)
                flash("Your patient account was created successfully. Please sign in.", "success")
                return redirect(url_for("auth.login"))
            except IntegrityError:
                db.session.rollback()
                flash("An account with that email already exists.", "error")
    return render_template("auth/register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter(db.func.lower(User.email) == email).first()
        if user and user.check_password(password):
            login_user(user, remember=request.form.get("remember") == "on")
            next_url = request.args.get("next", "")
            parsed_next = urlsplit(next_url)
            if (
                next_url.startswith("/")
                and not next_url.startswith("//")
                and not parsed_next.scheme
                and not parsed_next.netloc
            ):
                destination = next_url
            else:
                destination = url_for("main.dashboard")
            session.pop("_csrf_token", None)
            return redirect(destination)
        flash("Email or password is incorrect.", "error")
    return render_template("auth/login.html")


@auth_bp.post("/logout")
def logout():
    logout_user()
    session.pop("_csrf_token", None)
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))
