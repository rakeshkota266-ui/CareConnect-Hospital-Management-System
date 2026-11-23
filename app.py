import os
import secrets
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import click
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user
from sqlalchemy import event, inspect, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from dotenv import load_dotenv

from extensions import db, login_manager
from models import AuditEvent, DoctorProfile, User
from validators import valid_email
from billing_service import refresh_overdue_invoices
from audit import log_audit
from utils.decorators import roles_required
from utils.uploads import MAX_UPLOAD_BYTES

load_dotenv()


def create_app(test_config=None):
    production = os.environ.get("CARECONNECT_ENV", "").strip().casefold() == "production"
    secret_key = os.environ.get("SECRET_KEY")
    if production and not secret_key:
        raise RuntimeError("SECRET_KEY must be configured when CARECONNECT_ENV=production.")
    if production and os.environ.get("FLASK_DEBUG", "0").strip().casefold() in {"1", "true", "yes", "on"}:
        raise RuntimeError("FLASK_DEBUG must be disabled when CARECONNECT_ENV=production.")

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=secret_key or secrets.token_hex(32),
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL", f"sqlite:///{Path(app.instance_path) / 'careconnect.db'}"
        ),
        UPLOAD_FOLDER=os.environ.get(
            "UPLOAD_FOLDER", str(Path(app.instance_path) / "uploads")
        ),
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES + 64 * 1024,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        DEBUG=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "").lower() in {"1", "true", "yes"},
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "").lower() in {"1", "true", "yes"},
    )
    if test_config:
        app.config.update(test_config)
    if production:
        app.config["DEBUG"] = False
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "info"

    from routes.admin import admin_bp
    from routes.appointments import appointments_bp
    from routes.auth import auth_bp
    from routes.billing import billing_bp
    from routes.main import main_bp
    from routes.records import records_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(appointments_bp)
    app.register_blueprint(records_bp)
    app.register_blueprint(billing_bp)

    @app.before_request
    def protect_forms():
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            submitted = request.form.get("_csrf_token", "")
            expected = session.get("_csrf_token", "")
            if not expected or not secrets.compare_digest(submitted, expected):
                abort(400, description="The form expired or could not be verified. Please try again.")

    @app.context_processor
    def inject_template_values():
        token = session.setdefault("_csrf_token", secrets.token_urlsafe(32))
        return {
            "csrf_token": token,
            "now": datetime.now,
            "current_role": current_user.normalized_role if current_user.is_authenticated else "",
        }

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; form-action 'self'; "
            "frame-ancestors 'none'; img-src 'self' data:; "
            "font-src 'self' https://fonts.gstatic.com; "
            "style-src 'self' https://fonts.googleapis.com",
        )
        if request.is_secure or app.config["SESSION_COOKIE_SECURE"]:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.get("/healthz")
    def health_check():
        try:
            db.session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            db.session.rollback()
            app.logger.exception("Database health check failed.")
            return {"status": "unhealthy"}, 503
        return {"status": "ok"}, 200

    @app.template_filter("usd")
    def format_usd(value):
        return f"${Decimal(value):,.2f}"

    @app.cli.command("init-db")
    def init_db_command():
        """Create database tables."""
        db.create_all()
        click.echo("CareConnect database tables are ready.")

    @app.cli.command("create-admin")
    @click.option("--name", prompt="Administrator name")
    @click.option("--email", prompt="Administrator email")
    @click.password_option()
    def create_admin_command(name, email, password):
        """Create an administrator account without shipping default credentials."""
        name = name.strip()
        email = email.strip().lower()
        if not name or len(name) > 120 or any(ord(char) < 32 for char in name):
            raise click.ClickException("Enter an administrator name of 1–120 printable characters.")
        if not valid_email(email):
            raise click.ClickException("Enter a valid administrator email address.")
        if len(password) < 10:
            raise click.ClickException("Choose a password with at least 10 characters.")
        if db.session.query(User.id).filter(db.func.lower(User.email) == email).first():
            raise click.ClickException("An account with that email already exists.")
        admin = User(name=name, email=email, role="admin")
        admin.set_password(password)
        db.session.add(admin)
        try:
            db.session.flush()
            log_audit(
                "admin.account_created",
                "user",
                admin.id,
                "Initial administrator account was created.",
                actor_id=None,
                system=True,
            )
            db.session.commit()
        except IntegrityError as error:
            db.session.rollback()
            app.logger.exception("Unable to create the administrator account.")
            raise click.ClickException("That email is already in use.") from error
        click.echo(f"Administrator account created for {email}.")

    @app.cli.command("mark-overdue-invoices")
    def mark_overdue_invoices_command():
        """Mark unpaid invoices past their due date as overdue."""
        count = refresh_overdue_invoices()
        click.echo(f"Marked {count} unpaid invoice(s) overdue.")

    @app.cli.command("seed-doctors")
    @click.option("--count", type=click.IntRange(1, 5), default=3, show_default=True)
    def seed_doctors_command(count):
        """Add synthetic doctors for local development."""
        if os.environ.get("CARECONNECT_ALLOW_DEMO_SEED") != "1":
            raise click.ClickException(
                "Demo seeding is disabled. Set CARECONNECT_ALLOW_DEMO_SEED=1 only for a development database."
            )

        sample_doctors = [
            ("Jordan Lee", "Cardiology", "MD, FACC", 12, "185.00"),
            ("Taylor Morgan", "Family Medicine", "MD", 8, "120.00"),
            ("Casey Rivera", "Pediatrics", "MD, FAAP", 10, "145.00"),
            ("Avery Patel", "Dermatology", "MD", 7, "160.00"),
            ("Riley Kim", "Neurology", "MD, PhD", 14, "210.00"),
        ]
        created = 0
        for index, (name, specialization, qualification, experience, fee) in enumerate(
            sample_doctors[:count], start=1
        ):
            email = f"demo.doctor{index}@careconnect.test"
            existing = User.query.filter_by(email=email).first()
            if existing:
                if existing.role != "doctor" or existing.doctor_profile is None:
                    raise click.ClickException(
                        f"{email} already belongs to a non-doctor account; no seed data was changed."
                    )
                continue
            doctor = User(name=name, email=email, role="doctor")
            doctor.set_password(secrets.token_urlsafe(24))
            doctor.doctor_profile = DoctorProfile(
                specialization=specialization,
                department=specialization,
                license_number=f"CC-DEMO-{index:03d}",
                qualification=qualification,
                experience_years=experience,
                consultation_fee=Decimal(fee),
            )
            db.session.add(doctor)
            created += 1
        db.session.commit()
        click.echo(f"Added {created} synthetic doctor account(s); existing demo accounts were left unchanged.")

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def handle_client_error(error):
        message = (
            "Uploaded files exceed the maximum request size."
            if error.code == 413
            else getattr(error, "description", "The page could not be found.")
        )
        return render_template(
            "error.html",
            code=error.code,
            message=message,
        ), error.code

    @app.errorhandler(500)
    def handle_server_error(error):
        db.session.rollback()
        app.logger.exception("Unhandled application error", exc_info=error)
        return render_template(
            "error.html", code=500, message="Something went wrong. Please try again later."
        ), 500

    with app.app_context():
        if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
            @event.listens_for(db.engine, "connect")
            def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
        db.create_all()
        audit_columns = {
            column["name"] for column in inspect(db.engine).get_columns("audit_events")
        }
        if "actor_name" not in audit_columns:
            db.session.execute(
                text("ALTER TABLE audit_events ADD COLUMN actor_name VARCHAR(120)")
            )
            db.session.commit()

        audit_indexes = set(
            db.session.execute(
                text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='audit_events'")
            ).scalars()
        ) if db.engine.dialect.name == "sqlite" else {
            index.get("name") for index in inspect(db.engine).get_indexes("audit_events")
        }
        if "ix_audit_events_occurred_at" not in audit_indexes:
            db.session.execute(
                text("CREATE INDEX ix_audit_events_occurred_at ON audit_events (occurred_at)")
            )
        if "ix_audit_events_entity" not in audit_indexes:
            db.session.execute(
                text("CREATE INDEX ix_audit_events_entity ON audit_events (entity_type, entity_id)")
            )
        db.session.commit()
        if db.engine.dialect.name == "sqlite":
            db.session.execute(
                text(
                    "CREATE TRIGGER IF NOT EXISTS audit_events_no_delete "
                    "BEFORE DELETE ON audit_events BEGIN "
                    "SELECT RAISE(ABORT, 'audit events cannot be deleted'); END"
                )
            )
            db.session.execute(
                text(
                    "CREATE TRIGGER IF NOT EXISTS audit_events_no_update "
                    "BEFORE UPDATE ON audit_events "
                    "WHEN NEW.action IS NOT OLD.action "
                    "OR NEW.entity_type IS NOT OLD.entity_type "
                    "OR NEW.entity_id IS NOT OLD.entity_id "
                    "OR NEW.summary IS NOT OLD.summary "
                    "OR NEW.occurred_at IS NOT OLD.occurred_at "
                    "OR (NEW.actor_id IS NOT OLD.actor_id AND NEW.actor_id IS NOT NULL) "
                    "OR NEW.actor_name IS NOT OLD.actor_name "
                    "BEGIN SELECT RAISE(ABORT, 'audit events are immutable'); END"
                )
            )
            db.session.commit()
        if db.engine.dialect.name == "sqlite":
            user_indexes = set(
                db.session.execute(
                    text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='users'")
                ).scalars()
            )
        else:
            user_indexes = {index.get("name") for index in inspect(db.engine).get_indexes("users")}
        email_index_name = "uq_users_email_lower"
        if email_index_name not in user_indexes:
            duplicate_email = db.session.execute(
                text(
                    "SELECT lower(email) FROM users GROUP BY lower(email) "
                    "HAVING COUNT(*) > 1 LIMIT 1"
                )
            ).first()
            if duplicate_email:
                app.logger.error(
                    "Could not add the case-insensitive email uniqueness index because duplicate accounts exist; "
                    "existing user data was preserved."
                )
            else:
                db.session.execute(
                    text("CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email))")
                )
            db.session.commit()

        profile_columns = {column["name"] for column in inspect(db.engine).get_columns("doctor_profiles")}
        if "qualification" not in profile_columns:
            db.session.execute(
                text("ALTER TABLE doctor_profiles ADD COLUMN qualification VARCHAR(160) NOT NULL DEFAULT ''")
            )
        if "experience_years" not in profile_columns:
            db.session.execute(
                text("ALTER TABLE doctor_profiles ADD COLUMN experience_years INTEGER NOT NULL DEFAULT 0")
            )
        db.session.commit()

        inspector = inspect(db.engine)
        unique_constraints = {
            constraint.get("name") for constraint in inspector.get_unique_constraints("appointments")
        }
        indexes = {index.get("name") for index in inspector.get_indexes("appointments")}
        index_name = "uq_appointments_doctor_scheduled_at"
        if index_name not in unique_constraints and index_name not in indexes:
            duplicate = db.session.execute(
                text(
                    "SELECT 1 FROM appointments GROUP BY doctor_id, scheduled_at "
                    "HAVING COUNT(*) > 1 LIMIT 1"
                )
            ).first()
            if duplicate:
                app.logger.error(
                    "Could not add the appointment uniqueness index because duplicate doctor time slots already exist."
                )
            else:
                db.session.execute(
                    text(
                        "CREATE UNIQUE INDEX uq_appointments_doctor_scheduled_at "
                        "ON appointments (doctor_id, scheduled_at)"
                    )
                )
            db.session.commit()

        invoice_indexes = {index.get("name") for index in inspector.get_indexes("invoices")}
        invoice_index_name = "uq_invoices_appointment_id"
        if invoice_index_name not in invoice_indexes:
            duplicate_invoice = db.session.execute(
                text(
                    "SELECT appointment_id FROM invoices "
                    "WHERE appointment_id IS NOT NULL "
                    "GROUP BY appointment_id HAVING COUNT(*) > 1 LIMIT 1"
                )
            ).first()
            if duplicate_invoice:
                app.logger.error(
                    "Could not add the appointment invoice uniqueness index because duplicate invoices already exist; "
                    "existing invoice data was preserved."
                )
            else:
                db.session.execute(
                    text(
                        "CREATE UNIQUE INDEX uq_invoices_appointment_id "
                        "ON invoices (appointment_id)"
                    )
                )
            db.session.commit()

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
