import io
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError, OperationalError

from app import create_app
from extensions import db
from models import (
    Appointment,
    AuditEvent,
    DemoPaymentSimulation,
    DoctorProfile,
    Invoice,
    MedicalRecord,
    MedicalRecordAttachment,
    PatientProfile,
    User,
)
from utils.uploads import MAX_UPLOAD_BYTES


class CareConnectTestCase(unittest.TestCase):
    def setUp(self):
        self.upload_temp_dir = TemporaryDirectory()
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key-not-for-production",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "UPLOAD_FOLDER": self.upload_temp_dir.name,
            }
        )
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()
        self.patient = self.make_user("Patient One", "patient@example.com", "patient")
        self.other_patient = self.make_user("Patient Two", "other@example.com", "patient")
        self.doctor = self.make_user("Doctor One", "doctor@example.com", "doctor")
        self.doctor.doctor_profile = DoctorProfile(
            specialization="Family Medicine",
            department="Primary Care",
            license_number="LIC-001",
        )
        self.admin = self.make_user("Admin One", "admin@example.com", "admin")
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()
        self.upload_temp_dir.cleanup()

    @staticmethod
    def make_user(name, email, role):
        user = User(name=name, email=email, role=role, patient_profile=PatientProfile() if role == "patient" else None)
        user.set_password("a-secure-test-password")
        db.session.add(user)
        db.session.flush()
        return user

    def sign_in(self, user, password="a-secure-test-password"):
        response = self.client.post(
            "/auth/login",
            data={
                "_csrf_token": self.csrf_token(),
                "email": user.email,
                "password": password,
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

    def csrf_token(self):
        self.client.get("/")
        with self.client.session_transaction() as browser_session:
            return browser_session["_csrf_token"]

    def test_health_check_requires_a_working_database_and_is_debug_off(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "ok"})
        self.assertFalse(self.app.debug)

    def test_health_check_hides_database_error_details(self):
        with patch(
            "app.db.session.execute",
            side_effect=OperationalError("SELECT 1", {}, Exception("sensitive database detail")),
        ):
            response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json, {"status": "unhealthy"})
        self.assertNotIn(b"sensitive database detail", response.data)

    def test_production_configuration_requires_secret_key(self):
        with patch.dict(
            os.environ,
            {
                "CARECONNECT_ENV": "production",
                "SECRET_KEY": "",
                "FLASK_DEBUG": "0",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "SECRET_KEY must be configured"):
                create_app(
                    {
                        "TESTING": True,
                        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                    }
                )

    def test_production_configuration_rejects_debug_mode(self):
        with patch.dict(
            os.environ,
            {
                "CARECONNECT_ENV": "production",
                "SECRET_KEY": "configured-for-test-only",
                "FLASK_DEBUG": "1",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "FLASK_DEBUG must be disabled"):
                create_app(
                    {
                        "TESTING": True,
                        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                    }
                )

    def test_route_modules_import_without_importing_application_module(self):
        route_modules = (
            "routes.admin",
            "routes.appointments",
            "routes.auth",
            "routes.billing",
            "routes.main",
            "routes.records",
        )
        script = (
            "import importlib, sys; "
            f"modules = {route_modules!r}; "
            "[importlib.import_module(module) for module in modules]; "
            "assert 'app' not in sys.modules"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def create_appointment(self, status="scheduled", patient=None, doctor=None):
        appointment = Appointment(
            patient=patient or self.patient,
            doctor=doctor or self.doctor,
            scheduled_at=datetime.now() + timedelta(days=10, minutes=Appointment.query.count()),
            reason="Status transition test",
            status=status,
        )
        db.session.add(appointment)
        db.session.commit()
        return appointment

    def change_appointment_status(self, appointment, actor, status):
        if self.client.get("/dashboard").status_code != 302:
            self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})
        self.sign_in(actor)
        return self.client.post(
            f"/appointments/{appointment.id}/status",
            data={"_csrf_token": self.csrf_token(), "status": status},
        )

    def submit_registration(self, **overrides):
        data = {
            "_csrf_token": self.csrf_token(),
            "name": "New Patient",
            "email": "new@example.com",
            "phone": "555-0104",
            "password": "a-long-test-password",
            "confirm_password": "a-long-test-password",
        }
        data.update(overrides)
        return self.client.post("/auth/register", data=data, follow_redirects=True)

    def test_authentication_rejects_invalid_credentials_and_supports_all_roles(self):
        response = self.client.post(
            "/auth/login",
            data={
                "_csrf_token": self.csrf_token(),
                "email": self.admin.email,
                "password": "incorrect-password",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Email or password is incorrect.", response.data)
        self.assertEqual(self.client.get("/dashboard").status_code, 302)

        for user, forbidden_path in (
            (self.patient, "/admin/audit"),
            (self.doctor, "/admin/audit"),
            (self.admin, "/billing/1/payment-confirmation"),
        ):
            with self.subTest(role=user.normalized_role):
                self.sign_in(user)
                self.assertEqual(self.client.get("/dashboard").status_code, 200)
                self.assertEqual(self.client.get(forbidden_path).status_code, 403)
                response = self.client.post(
                    "/auth/logout",
                    data={"_csrf_token": self.csrf_token()},
                )
                self.assertEqual(response.status_code, 302)
                self.assertIn("/auth/login", response.headers["Location"])
                self.assertEqual(self.client.get("/dashboard").status_code, 302)

    def test_registration_creates_hashed_patient_and_redirects_to_login(self):
        response = self.client.post(
            "/auth/register",
            data={
                "_csrf_token": self.csrf_token(),
                "name": "New Patient",
                "email": " NEW@example.com ",
                "phone": "555-0104",
                "password": "a-long-test-password",
                "confirm_password": "a-long-test-password",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/auth/login"))

        response = self.client.get(response.headers["Location"])
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Please sign in", response.data)
        created = User.query.filter_by(email="new@example.com").one()
        self.assertEqual(created.name, "New Patient")
        self.assertEqual(created.role, "patient")
        self.assertIsNotNone(created.patient_profile)
        self.assertEqual(created.phone, "555-0104")
        self.assertNotEqual(created.password_hash, "a-long-test-password")
        self.assertTrue(created.check_password("a-long-test-password"))
        self.assertEqual(self.client.get("/dashboard").status_code, 302)

        self.sign_in(created, password="a-long-test-password")
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_registration_rejects_invalid_email_and_required_fields(self):
        for overrides, message in (
            ({"email": "not-an-email"}, "valid email address"),
            ({"email": "person@invalid"}, "valid email address"),
            ({"name": "   "}, "Enter your name"),
            ({"name": ""}, "Enter your name"),
        ):
            with self.subTest(overrides=overrides):
                response = self.submit_registration(**overrides)
                self.assertEqual(response.status_code, 200)
                self.assertIn(message.encode(), response.data)
        self.assertEqual(User.query.filter_by(email="new@example.com").count(), 0)

    def test_registration_rejects_invalid_phone_and_weak_password(self):
        for overrides, message in (
            ({"phone": "abc"}, "valid phone number"),
            ({"password": "short", "confirm_password": "short"}, "at least 10 characters"),
        ):
            with self.subTest(overrides=overrides):
                response = self.submit_registration(**overrides)
                self.assertIn(message.encode(), response.data)
                self.assertIsNone(User.query.filter_by(email="new@example.com").first())

    def test_registration_rejects_mismatched_passwords(self):
        response = self.submit_registration(confirm_password="different-password")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"passwords do not match", response.data)
        self.assertIsNone(User.query.filter_by(email="new@example.com").first())

    def test_registration_rejects_duplicate_email_case_insensitively(self):
        response = self.submit_registration(email="PATIENT@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"account with that email already exists", response.data)
        self.assertEqual(User.query.filter(db.func.lower(User.email) == "patient@example.com").count(), 1)

    def test_registration_form_keeps_phone_and_uses_mobile_layout(self):
        response = self.client.get("/auth/register")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="phone" type="tel"', response.data)
        self.assertIn(b"maxlength=\"254\"", response.data)
        with open(
            os.path.join(os.path.dirname(__file__), "..", "static", "css", "style.css"),
            encoding="utf-8",
        ) as stylesheet:
            css = stylesheet.read()
        self.assertIn("@media (max-width: 760px)", css)
        self.assertIn(".auth-layout { grid-template-columns: 1fr;", css)
        self.assertIn("@media (max-width: 450px)", css)

    def test_csrf_is_required_for_state_changes(self):
        response = self.client.post("/auth/register", data={"name": "No token"})
        self.assertEqual(response.status_code, 400)

    def test_patient_can_book_appointment_and_cannot_view_another_patients_records(self):
        record = MedicalRecord(
            patient=self.other_patient,
            doctor=self.doctor,
            diagnosis="Private diagnosis",
            treatment="Private treatment",
        )
        db.session.add(record)
        db.session.commit()

        self.sign_in(self.patient)
        response = self.client.post(
            "/appointments/new",
            data={
                "_csrf_token": self.csrf_token(),
                "doctor_id": str(self.doctor.id),
                "scheduled_at": (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
                "reason": "Annual checkup",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        appointment = Appointment.query.one()
        self.assertEqual(appointment.patient_id, self.patient.id)
        self.assertEqual(appointment.doctor_id, self.doctor.id)

        response = self.client.get(f"/records/{record.id}")
        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_author_new_medical_records(self):
        self.sign_in(self.admin)
        response = self.client.get("/records/new")
        self.assertEqual(response.status_code, 403)

    def test_doctor_can_create_record_for_patient(self):
        self.sign_in(self.doctor)
        response = self.client.post(
            "/records/new",
            data={
                "_csrf_token": self.csrf_token(),
                "patient_id": str(self.patient.id),
                "diagnosis": "Seasonal allergies",
                "treatment": "Rest and hydration",
                "notes": "Review in two weeks",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        record = MedicalRecord.query.one()
        self.assertEqual(record.doctor_id, self.doctor.id)
        self.assertEqual(record.patient_id, self.patient.id)

    def test_medical_record_attachment_upload_is_sanitized_and_downloadable(self):
        self.sign_in(self.doctor)
        response = self.client.post(
            "/records/new",
            data={
                "_csrf_token": self.csrf_token(),
                "patient_id": str(self.patient.id),
                "diagnosis": "Attachment test",
                "treatment": "Routine follow-up",
                "attachments": (io.BytesIO(b"%PDF-1.7\nsynthetic test document"), "../../visit summary.pdf"),
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        record = MedicalRecord.query.one()
        attachment = MedicalRecordAttachment.query.one()
        self.assertEqual(attachment.medical_record_id, record.id)
        self.assertEqual(attachment.original_filename, "visit_summary.pdf")
        self.assertNotIn("..", attachment.stored_filename)
        stored_path = Path(self.app.config["UPLOAD_FOLDER"]) / attachment.stored_filename
        self.assertTrue(stored_path.is_file())
        self.assertIn(b"visit_summary.pdf", self.client.get(f"/records/{record.id}").data)

        download = self.client.get(
            f"/records/{record.id}/attachments/{attachment.id}"
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data, b"%PDF-1.7\nsynthetic test document")
        self.assertEqual(
            download.headers["Content-Disposition"],
            "attachment; filename=visit_summary.pdf",
        )
        download.close()
        self.assertEqual(
            AuditEvent.query.filter_by(
                action="medical_record.attachment_added",
                entity_id=str(attachment.id),
            ).one().summary,
            "A document attachment was added to a medical record.",
        )

    def test_medical_record_attachment_rejects_bad_extension_and_spoofed_content(self):
        self.sign_in(self.doctor)
        for content, filename in (
            (b"%PDF-1.7\nnot a permitted extension", "notes.txt"),
            (b"not a PDF", "notes.pdf"),
        ):
            with self.subTest(filename=filename):
                response = self.client.post(
                    "/records/new",
                    data={
                        "_csrf_token": self.csrf_token(),
                        "patient_id": str(self.patient.id),
                        "diagnosis": "Attachment test",
                        "treatment": "Routine follow-up",
                        "attachments": (io.BytesIO(content), filename),
                    },
                    follow_redirects=True,
                )
                self.assertEqual(response.status_code, 200)
                expected_message = (
                    b"Attachments must be PDF, PNG, or JPEG files."
                    if filename.endswith(".txt")
                    else b"file contents do not match"
                )
                self.assertIn(expected_message, response.data)
                self.assertEqual(MedicalRecord.query.count(), 0)
                self.assertEqual(MedicalRecordAttachment.query.count(), 0)
        upload_folder = Path(self.app.config["UPLOAD_FOLDER"])
        self.assertEqual(list(upload_folder.glob("*")) if upload_folder.exists() else [], [])

    def test_medical_record_attachment_rejects_files_over_ten_megabytes(self):
        self.sign_in(self.doctor)
        response = self.client.post(
            "/records/new",
            data={
                "_csrf_token": self.csrf_token(),
                "patient_id": str(self.patient.id),
                "diagnosis": "Attachment size test",
                "treatment": "Routine follow-up",
                "attachments": (
                    io.BytesIO(b"%PDF-" + b"x" * MAX_UPLOAD_BYTES),
                    "large.pdf",
                ),
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Each attachment must be 10 MB or smaller.", response.data)
        self.assertEqual(MedicalRecord.query.count(), 0)
        self.assertEqual(MedicalRecordAttachment.query.count(), 0)

    def test_medical_record_attachment_download_enforces_record_ownership(self):
        self.sign_in(self.doctor)
        response = self.client.post(
            "/records/new",
            data={
                "_csrf_token": self.csrf_token(),
                "patient_id": str(self.other_patient.id),
                "diagnosis": "Private attachment test",
                "treatment": "Routine follow-up",
                "attachments": (io.BytesIO(b"%PDF-1.7\nprivate test document"), "private.pdf"),
            },
        )
        self.assertEqual(response.status_code, 302)
        record = MedicalRecord.query.one()
        attachment = MedicalRecordAttachment.query.one()

        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})
        self.sign_in(self.patient)
        self.assertEqual(
            self.client.get(f"/records/{record.id}/attachments/{attachment.id}").status_code,
            403,
        )
        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})
        self.sign_in(self.other_patient)
        download = self.client.get(f"/records/{record.id}/attachments/{attachment.id}")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data, b"%PDF-1.7\nprivate test document")
        download.close()

    def test_medical_record_attachments_survive_application_restart(self):
        with TemporaryDirectory() as directory:
            database_url = f"sqlite:///{(Path(directory) / 'attachments.sqlite').as_posix()}"
            upload_folder = str(Path(directory) / "persistent-uploads")
            config = {
                "TESTING": True,
                "SECRET_KEY": "attachment-persistence-test-key",
                "SQLALCHEMY_DATABASE_URI": database_url,
                "UPLOAD_FOLDER": upload_folder,
            }
            first_app = create_app(config)
            with first_app.app_context():
                patient = User(name="Persisted Patient", email="persisted@example.com", role="patient")
                patient.set_password("a-secure-test-password")
                doctor = User(name="Persisted Doctor", email="persisted-doctor@example.com", role="doctor")
                doctor.set_password("a-secure-test-password")
                db.session.add_all([patient, doctor])
                db.session.flush()
                record = MedicalRecord(
                    patient=patient,
                    doctor=doctor,
                    diagnosis="Synthetic persistence test",
                    treatment="Synthetic test only",
                )
                db.session.add(record)
                db.session.flush()
                upload_path = Path(upload_folder) / "persisted-test.pdf"
                upload_path.parent.mkdir(parents=True, exist_ok=True)
                upload_path.write_bytes(b"%PDF-1.7\npersisted synthetic document")
                attachment = MedicalRecordAttachment(
                    medical_record=record,
                    original_filename="persisted.pdf",
                    stored_filename=upload_path.name,
                    content_type="application/pdf",
                )
                db.session.add(attachment)
                db.session.commit()
                record_id, attachment_id = record.id, attachment.id
                db.session.remove()
                db.engine.dispose()

            restarted_app = create_app(config)
            client = restarted_app.test_client()
            client.get("/auth/login")
            with client.session_transaction() as browser_session:
                csrf_token = browser_session["_csrf_token"]
            login = client.post(
                "/auth/login",
                data={
                    "_csrf_token": csrf_token,
                    "email": "persisted@example.com",
                    "password": "a-secure-test-password",
                },
            )
            self.assertEqual(login.status_code, 302)
            download = client.get(
                f"/records/{record_id}/attachments/{attachment_id}"
            )
            self.assertEqual(download.status_code, 200)
            self.assertEqual(download.data, b"%PDF-1.7\npersisted synthetic document")
            download.close()
            with restarted_app.app_context():
                db.session.remove()
                db.engine.dispose()

    def test_doctor_can_update_owned_record_and_invalid_updates_are_rejected(self):
        record = MedicalRecord(
            patient=self.patient,
            doctor=self.doctor,
            diagnosis="Initial diagnosis",
            treatment="Initial treatment",
            notes="Initial notes",
        )
        db.session.add(record)
        db.session.commit()
        record_id = record.id

        self.sign_in(self.doctor)
        page = self.client.get(f"/records/{record_id}/edit")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Initial diagnosis", page.data)
        response = self.client.post(
            f"/records/{record_id}/edit",
            data={
                "_csrf_token": self.csrf_token(),
                "diagnosis": "Updated diagnosis",
                "treatment": "Updated treatment plan",
                "notes": "Updated notes",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Medical record updated.", response.data)
        self.assertEqual(record.diagnosis, "Updated diagnosis")
        self.assertEqual(record.treatment, "Updated treatment plan")
        self.assertEqual(record.notes, "Updated notes")
        self.assertEqual(db.session.get(MedicalRecord, record_id).diagnosis, "Updated diagnosis")
        audit_event = AuditEvent.query.filter_by(
            action="medical_record.updated", entity_id=str(record_id)
        ).one()
        self.assertEqual(audit_event.actor_id, self.doctor.id)
        self.assertEqual(audit_event.summary, "A medical record was updated.")
        self.assertNotIn("Updated diagnosis", audit_event.summary)

        response = self.client.post(
            f"/records/{record_id}/edit",
            data={
                "_csrf_token": self.csrf_token(),
                "diagnosis": " ",
                "treatment": "Updated treatment plan",
                "notes": "",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Provide a diagnosis and treatment", response.data)
        self.assertEqual(record.diagnosis, "Updated diagnosis")

        response = self.client.post(
            f"/records/{record_id}/edit",
            data={
                "_csrf_token": self.csrf_token(),
                "diagnosis": "D" * 301,
                "treatment": "Updated treatment plan",
                "notes": "",
            },
            follow_redirects=True,
        )
        self.assertIn(b"allowed length limits", response.data)
        self.assertEqual(record.diagnosis, "Updated diagnosis")

    def test_admin_can_manage_records_and_doctors_cannot_edit_other_doctors_records(self):
        other_doctor = self.make_user("Doctor Two", "doctor.two@example.com", "doctor")
        record = MedicalRecord(
            patient=self.patient,
            doctor=other_doctor,
            diagnosis="Other doctor's diagnosis",
            treatment="Other doctor's treatment",
        )
        db.session.add(record)
        db.session.commit()
        record_id = record.id

        self.sign_in(self.doctor)
        self.assertEqual(self.client.get(f"/records/{record_id}").status_code, 403)
        self.assertEqual(self.client.get(f"/records/{record_id}/edit").status_code, 403)
        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})

        self.sign_in(self.patient)
        self.assertEqual(self.client.get(f"/records/{record_id}/edit").status_code, 403)
        self.assertEqual(self.client.get("/records/new").status_code, 403)
        own_records = self.client.get("/records/")
        self.assertEqual(own_records.status_code, 200)
        self.assertNotIn(b"Other doctor's diagnosis", own_records.data)
        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})

        self.sign_in(self.admin)
        listing = self.client.get("/records/")
        self.assertIn(b"Other doctor&#39;s diagnosis", listing.data)
        response = self.client.post(
            f"/records/{record_id}/edit",
            data={
                "_csrf_token": self.csrf_token(),
                "diagnosis": "Admin corrected diagnosis",
                "treatment": "Admin corrected treatment",
                "notes": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(record.diagnosis, "Admin corrected diagnosis")

    def test_record_creation_rejects_malformed_patient_and_invalid_fields(self):
        self.sign_in(self.doctor)
        invalid_submissions = (
            {"patient_id": "not-an-id", "diagnosis": "Diagnosis", "treatment": "Treatment"},
            {"patient_id": str(self.patient.id), "diagnosis": " ", "treatment": "Treatment"},
            {"patient_id": str(self.patient.id), "diagnosis": "D" * 301, "treatment": "Treatment"},
            {"patient_id": str(self.patient.id), "diagnosis": "Diagnosis", "treatment": " "},
            {
                "patient_id": str(self.patient.id),
                "diagnosis": "Diagnosis",
                "treatment": "T" * 10001,
            },
            {
                "patient_id": str(self.patient.id),
                "diagnosis": "Diagnosis",
                "treatment": "Treatment",
                "notes": "N" * 5001,
            },
        )
        for data in invalid_submissions:
            with self.subTest(data={key: value[:20] for key, value in data.items()}):
                response = self.client.post(
                    "/records/new",
                    data={"_csrf_token": self.csrf_token(), **data},
                    follow_redirects=True,
                )
                self.assertIn(response.status_code, (200, 400))
                self.assertEqual(MedicalRecord.query.count(), 0)

    def test_appointment_invoice_uses_linked_patient_and_doctor_fee_and_prevents_duplicates(self):
        self.doctor.doctor_profile.consultation_fee = Decimal("87.50")
        appointment = self.create_appointment()
        appointment_id = appointment.id
        self.sign_in(self.admin)
        form = self.client.get("/admin/invoices/new")
        self.assertEqual(form.status_code, 200)
        self.assertIn(b"Appointment", form.data)
        self.assertIn(b"$87.50", form.data)

        response = self.client.post(
            "/admin/invoices/new",
            data={
                "_csrf_token": self.csrf_token(),
                "appointment_id": str(appointment_id),
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        invoice = Invoice.query.one()
        self.assertEqual(invoice.patient_id, self.patient.id)
        self.assertEqual(invoice.appointment_id, appointment_id)
        self.assertEqual(invoice.appointment.doctor_id, self.doctor.id)
        self.assertEqual(invoice.appointment.doctor.name, self.doctor.name)
        self.assertEqual(invoice.amount, Decimal("87.50"))
        self.assertIn("Dr. Doctor One", invoice.description)
        db.session.expire_all()
        persisted_invoice = db.session.get(Invoice, invoice.id)
        self.assertEqual(persisted_invoice.appointment.patient_id, self.patient.id)
        self.assertEqual(persisted_invoice.appointment.doctor_id, self.doctor.id)
        self.assertEqual(persisted_invoice.amount, Decimal("87.50"))
        indexes = {index["name"] for index in inspect(db.engine).get_indexes("invoices")}
        self.assertIn("uq_invoices_appointment_id", indexes)

        duplicate = self.client.post(
            "/admin/invoices/new",
            data={
                "_csrf_token": self.csrf_token(),
                "appointment_id": str(appointment_id),
                "patient_id": str(self.patient.id),
                "description": "Second charge",
                "amount": "87.50",
            },
            follow_redirects=True,
        )
        self.assertIn(b"invoice already exists", duplicate.data)
        self.assertEqual(Invoice.query.count(), 1)
        with self.assertRaises(IntegrityError):
            db.session.add(
                Invoice(
                    patient=self.patient,
                    appointment=appointment,
                    description="Database-level duplicate",
                    amount=Decimal("87.50"),
                )
            )
            db.session.commit()
        db.session.rollback()
        self.assertEqual(Invoice.query.count(), 1)

    def test_invoice_statuses_permissions_and_patient_scoping(self):
        own_invoice = Invoice(patient=self.patient, description="Own invoice", amount=Decimal("50.00"))
        other_invoice = Invoice(
            patient=self.other_patient, description="Private invoice", amount=Decimal("75.00")
        )
        cancelled_invoice = Invoice(
            patient=self.other_patient, description="Cancelled invoice", amount=Decimal("25.00")
        )
        db.session.add_all([own_invoice, other_invoice, cancelled_invoice])
        db.session.commit()
        own_invoice_id = own_invoice.id
        other_invoice_id = other_invoice.id
        cancelled_invoice_id = cancelled_invoice.id

        self.sign_in(self.patient)
        listing = self.client.get("/billing/")
        self.assertIn(b"Own invoice", listing.data)
        self.assertNotIn(b"Private invoice", listing.data)
        self.assertEqual(self.client.post(
            f"/billing/{own_invoice_id}/status",
            data={"_csrf_token": self.csrf_token(), "status": "paid"},
        ).status_code, 403)
        self.assertEqual(self.client.get("/admin/invoices/new").status_code, 403)
        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})

        self.sign_in(self.doctor)
        self.assertEqual(self.client.get("/billing/").status_code, 403)
        self.assertEqual(self.client.post(
            f"/billing/{own_invoice_id}/status",
            data={"_csrf_token": self.csrf_token(), "status": "cancelled"},
        ).status_code, 403)
        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})

        self.sign_in(self.admin)
        admin_listing = self.client.get("/billing/")
        self.assertIn(b"Own invoice", admin_listing.data)
        self.assertIn(b"Private invoice", admin_listing.data)
        invalid_transition = self.client.post(
            f"/billing/{own_invoice_id}/status",
            data={"_csrf_token": self.csrf_token(), "status": "overdue"},
        )
        self.assertEqual(invalid_transition.status_code, 400)
        db.session.get(Invoice, own_invoice_id).due_date = date.today() - timedelta(days=1)
        response = self.client.post(
            f"/billing/{own_invoice_id}/status",
            data={"_csrf_token": self.csrf_token(), "status": "overdue"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(db.session.get(Invoice, own_invoice_id).status, "overdue")
        response = self.client.post(
            f"/billing/{own_invoice_id}/status",
            data={"_csrf_token": self.csrf_token(), "status": "paid"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(db.session.get(Invoice, own_invoice_id).status, "paid")
        self.assertEqual(
            self.client.post(
                f"/billing/{own_invoice_id}/status",
                data={"_csrf_token": self.csrf_token(), "status": "unpaid"},
            ).status_code,
            400,
        )
        invalid = self.client.post(
            f"/billing/{other_invoice_id}/status",
            data={"_csrf_token": self.csrf_token(), "status": "refunded"},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(db.session.get(Invoice, other_invoice_id).status, "unpaid")
        self.assertEqual(
            self.client.post(
                f"/billing/{cancelled_invoice_id}/status",
                data={"_csrf_token": self.csrf_token(), "status": "cancelled"},
            ).status_code,
            302,
        )
        self.assertEqual(db.session.get(Invoice, cancelled_invoice_id).status, "cancelled")
        self.assertEqual(
            self.client.post(
                f"/billing/{cancelled_invoice_id}/paid",
                data={"_csrf_token": self.csrf_token()},
            ).status_code, 400,
        )
        self.assertEqual(db.session.get(Invoice, cancelled_invoice_id).status, "cancelled")
        self.assertEqual(
            self.client.post(
                f"/billing/{other_invoice_id}/paid",
                data={"_csrf_token": self.csrf_token()},
            ).status_code,
            302,
        )
        self.assertEqual(db.session.get(Invoice, other_invoice_id).status, "paid")
        self.assertEqual(db.session.execute(db.text("PRAGMA integrity_check")).scalar(), "ok")
        self.assertEqual(db.session.execute(db.text("PRAGMA foreign_key_check")).all(), [])

    def test_due_date_automatically_marks_only_eligible_invoices_overdue(self):
        past_due = Invoice(
            patient=self.patient,
            description="Past due unpaid",
            amount=Decimal("50.00"),
            due_date=date.today() - timedelta(days=1),
            status="unpaid",
        )
        paid = Invoice(
            patient=self.patient,
            description="Past due paid",
            amount=Decimal("25.00"),
            due_date=date.today() - timedelta(days=2),
            status="paid",
        )
        cancelled = Invoice(
            patient=self.patient,
            description="Past due cancelled",
            amount=Decimal("30.00"),
            due_date=date.today() - timedelta(days=2),
            status="cancelled",
        )
        due_today = Invoice(
            patient=self.patient,
            description="Due today",
            amount=Decimal("20.00"),
            due_date=date.today(),
        )
        future_due = Invoice(
            patient=self.patient,
            description="Future due",
            amount=Decimal("15.00"),
            due_date=date.today() + timedelta(days=1),
        )
        db.session.add_all([past_due, paid, cancelled, due_today, future_due])
        db.session.commit()
        past_due_id, paid_id, cancelled_id, due_today_id, future_due_id = (
            past_due.id,
            paid.id,
            cancelled.id,
            due_today.id,
            future_due.id,
        )

        self.sign_in(self.patient)
        response = self.client.get("/billing/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Past due unpaid", response.data)
        self.assertIn(b"Overdue", response.data)
        self.assertEqual(db.session.get(Invoice, past_due_id).status, "overdue")
        self.assertEqual(db.session.get(Invoice, paid_id).status, "paid")
        self.assertEqual(db.session.get(Invoice, cancelled_id).status, "cancelled")
        self.assertEqual(db.session.get(Invoice, due_today_id).status, "unpaid")
        self.assertEqual(db.session.get(Invoice, future_due_id).status, "unpaid")
        self.assertEqual(
            AuditEvent.query.filter_by(action="invoice.marked_overdue", entity_id=str(past_due_id)).count(),
            1,
        )
        self.client.get("/billing/")
        self.assertEqual(
            AuditEvent.query.filter_by(action="invoice.marked_overdue", entity_id=str(past_due_id)).count(),
            1,
        )
        command = self.app.test_cli_runner().invoke(args=["mark-overdue-invoices"])
        self.assertEqual(command.exit_code, 0, command.output)
        self.assertIn("Marked 0 unpaid invoice(s) overdue.", command.output)

    def test_demo_payment_simulation_is_owned_idempotent_audited_and_not_real(self):
        invoice = Invoice(
            patient=self.patient,
            description="Payment simulation invoice",
            amount=Decimal("123.45"),
            due_date=date.today() - timedelta(days=1),
        )
        other_invoice = Invoice(
            patient=self.other_patient,
            description="Other patient's invoice",
            amount=Decimal("75.00"),
        )
        db.session.add_all([invoice, other_invoice])
        db.session.commit()
        invoice_id, other_invoice_id = invoice.id, other_invoice.id

        self.sign_in(self.patient)
        listing = self.client.get("/billing/")
        self.assertIn(b"Simulate demo payment", listing.data)
        self.assertIn(b"no real payment is processed", listing.data.lower())
        denied = self.client.post(
            f"/billing/{other_invoice_id}/simulate-payment",
            data={"_csrf_token": self.csrf_token()},
        )
        self.assertEqual(denied.status_code, 403)

        response = self.client.post(
            f"/billing/{invoice_id}/simulate-payment",
            data={"_csrf_token": self.csrf_token()},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith(f"/billing/{invoice_id}/payment-confirmation"))
        self.assertEqual(db.session.get(Invoice, invoice_id).status, "paid")
        simulation = DemoPaymentSimulation.query.filter_by(invoice_id=invoice_id).one()
        self.assertEqual(simulation.patient_id, self.patient.id)
        self.assertEqual(simulation.amount, Decimal("123.45"))
        self.assertRegex(simulation.reference, r"^DEMO-[A-F0-9]{24}$")
        confirmation = self.client.get(response.headers["Location"])
        self.assertEqual(confirmation.status_code, 200)
        self.assertIn(b"No real payment was processed", confirmation.data)
        self.assertNotIn(b"card number", confirmation.data.lower())
        self.assertEqual(
            AuditEvent.query.filter_by(action="invoice.payment_simulated", entity_id=str(invoice_id)).count(),
            1,
        )
        duplicate = self.client.post(
            f"/billing/{invoice_id}/simulate-payment",
            data={"_csrf_token": self.csrf_token()},
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(DemoPaymentSimulation.query.filter_by(invoice_id=invoice_id).count(), 1)

        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})
        self.sign_in(self.doctor)
        self.assertEqual(
            self.client.post(
                f"/billing/{invoice_id}/simulate-payment",
                data={"_csrf_token": self.csrf_token()},
            ).status_code,
            403,
        )

    def test_invoice_creation_validates_due_date_and_logs_creation(self):
        self.sign_in(self.admin)
        invalid = self.client.post(
            "/admin/invoices/new",
            data={
                "_csrf_token": self.csrf_token(),
                "patient_id": str(self.patient.id),
                "description": "Invalid due date",
                "amount": "10.00",
                "due_date": "invalid-date",
            },
            follow_redirects=True,
        )
        self.assertIn(b"due date", invalid.data.lower())
        self.assertEqual(Invoice.query.count(), 0)
        response = self.client.post(
            "/admin/invoices/new",
            data={
                "_csrf_token": self.csrf_token(),
                "patient_id": str(self.patient.id),
                "description": "Valid due invoice",
                "amount": "10.00",
                "due_date": (date.today() - timedelta(days=1)).isoformat(),
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        invoice = Invoice.query.one()
        self.assertEqual(invoice.due_date, date.today() - timedelta(days=1))
        self.assertEqual(invoice.status, "overdue")
        self.assertEqual(
            AuditEvent.query.filter_by(action="admin.invoice_created", entity_id=str(invoice.id)).count(),
            1,
        )
        self.assertEqual(
            AuditEvent.query.filter_by(action="invoice.marked_overdue", entity_id=str(invoice.id)).count(),
            1,
        )

    def test_audit_log_captures_admin_appointment_record_and_invoice_changes(self):
        appointment = self.create_appointment()
        appointment_id = appointment.id
        self.sign_in(self.doctor)
        response = self.client.post(
            f"/appointments/{appointment_id}/status",
            data={"_csrf_token": self.csrf_token(), "status": "confirmed"},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            "/records/new",
            data={
                "_csrf_token": self.csrf_token(),
                "patient_id": str(self.patient.id),
                "diagnosis": "Audit test diagnosis",
                "treatment": "Audit test treatment",
            },
        )
        self.assertEqual(response.status_code, 302)
        record_id = MedicalRecord.query.one().id
        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})

        self.sign_in(self.admin)
        doctor_response = self.client.post(
            "/admin/doctors",
            data={
                "_csrf_token": self.csrf_token(),
                "name": "Audit Test Doctor",
                "email": "audit.doctor@example.com",
                "specialization": "Neurology",
                "qualification": "MD",
                "experience_years": "7",
                "consultation_fee": "90.00",
                "password": "a-temporary-audit-password",
            },
        )
        self.assertEqual(doctor_response.status_code, 302)
        self.assertTrue(AuditEvent.query.filter_by(action="admin.doctor_created").first())
        response = self.client.post(
            "/admin/invoices/new",
            data={
                "_csrf_token": self.csrf_token(),
                "patient_id": str(self.patient.id),
                "description": "Audited manual invoice",
                "amount": "90.00",
            },
        )
        self.assertEqual(response.status_code, 302)
        invoice = Invoice.query.filter_by(description="Audited manual invoice").one()
        invoice_id = invoice.id
        response = self.client.post(
            f"/billing/{invoice_id}/status",
            data={"_csrf_token": self.csrf_token(), "status": "paid"},
        )
        self.assertEqual(response.status_code, 302)

        expected = {
            "appointment.status_changed",
            "medical_record.created",
            "admin.doctor_created",
            "admin.invoice_created",
            "invoice.status_changed",
        }
        found = {event.action for event in AuditEvent.query.all()}
        self.assertTrue(expected.issubset(found), expected - found)
        record_event = AuditEvent.query.filter_by(
            action="medical_record.created", entity_id=str(record_id)
        ).one()
        self.assertEqual(record_event.actor_id, self.doctor.id)
        self.assertEqual(record_event.actor_name, self.doctor.name)
        self.assertNotIn("diagnosis", record_event.summary.casefold())
        log_page = self.client.get("/admin/audit")
        self.assertEqual(log_page.status_code, 200)
        self.assertIn(b"appointment.status_changed", log_page.data)
        self.assertIn(b"medical_record.created", log_page.data)
        self.assertNotIn(b"Audit test diagnosis", log_page.data)

        event_id = record_event.id
        with self.assertRaises(IntegrityError):
            db.session.execute(
                db.text("UPDATE audit_events SET summary = 'tampered' WHERE id = :id"),
                {"id": event_id},
            )
            db.session.commit()
        db.session.rollback()
        with self.assertRaises(IntegrityError):
            db.session.execute(db.text("DELETE FROM audit_events WHERE id = :id"), {"id": event_id})
            db.session.commit()
        db.session.rollback()
        self.assertEqual(db.session.get(AuditEvent, event_id).summary, record_event.summary)

        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})
        self.sign_in(self.patient)
        self.assertEqual(self.client.get("/admin/audit").status_code, 403)

    def test_admin_audit_page_renders_empty_state_and_rejects_unauthorized_users(self):
        self.sign_in(self.admin)
        response = self.client.get("/admin/audit")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No audit events yet", response.data)

        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})
        self.sign_in(self.patient)
        self.assertEqual(self.client.get("/admin/audit").status_code, 403)

        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})
        self.sign_in(self.doctor)
        self.assertEqual(self.client.get("/admin/audit").status_code, 403)

        anonymous_client = self.app.test_client()
        response = anonymous_client.get("/admin/audit")
        self.assertIn(response.status_code, {302, 403})
        if response.status_code == 302:
            self.assertIn("/auth/login", response.headers["Location"])

    def test_admin_audit_page_renders_populated_events(self):
        db.session.add(
            AuditEvent(
                actor=self.admin,
                actor_name=self.admin.name,
                action="test.audit_page_view",
                entity_type="test",
                entity_id="audit-page",
                summary="Synthetic test event",
            )
        )
        db.session.commit()
        self.sign_in(self.admin)

        response = self.client.get("/admin/audit")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"test.audit_page_view", response.data)
        self.assertIn(b"Admin One", response.data)
        self.assertIn(b"Synthetic test event", response.data)

    def test_admin_account_creation_records_safe_audit_event(self):
        self.sign_in(self.admin)
        response = self.client.post(
            "/admin/users",
            data={
                "_csrf_token": self.csrf_token(),
                "name": "Audit Patient",
                "email": "audit.patient@example.com",
                "phone": "555-0123",
                "role": "patient",
                "password": "a-temporary-account-password",
            },
        )
        self.assertEqual(response.status_code, 302)

        created = User.query.filter_by(email="audit.patient@example.com").one()
        event = AuditEvent.query.filter_by(
            action="admin.account_created", entity_id=str(created.id)
        ).one()
        self.assertEqual(event.actor_id, self.admin.id)
        self.assertEqual(event.actor_name, self.admin.name)
        self.assertEqual(event.entity_type, "user")
        self.assertIn("patient account", event.summary)
        self.assertNotIn("audit.patient@example.com", event.summary)
        self.assertNotIn("a-temporary-account-password", event.summary)
        self.assertIsNotNone(event.occurred_at)
        self.assertEqual(
            AuditEvent.query.filter_by(action="admin.doctor_created").count(), 0
        )

    def test_admin_audit_page_shows_only_latest_200_events_in_descending_order(self):
        start = datetime(2026, 1, 1)
        db.session.add_all(
            AuditEvent(
                action="test.audit_order",
                entity_type="test",
                entity_id=str(index),
                summary=f"audit-order-{index}",
                occurred_at=start + timedelta(seconds=index),
            )
            for index in range(205)
        )
        db.session.commit()
        self.sign_in(self.admin)

        response = self.client.get("/admin/audit")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"200 events", response.data)
        self.assertIn(b"<td>audit-order-204</td>", response.data)
        self.assertIn(b"<td>audit-order-5</td>", response.data)
        self.assertNotIn(b"<td>audit-order-4</td>", response.data)
        self.assertLess(
            response.data.index(b"<td>audit-order-204</td>"),
            response.data.index(b"<td>audit-order-5</td>"),
        )

    def test_audit_events_persist_after_recreating_file_backed_app(self):
        with TemporaryDirectory() as directory:
            database_url = f"sqlite:///{(Path(directory) / 'audit-persistence.sqlite').as_posix()}"
            config = {
                "TESTING": True,
                "SECRET_KEY": "audit-persistence-test-key",
                "SQLALCHEMY_DATABASE_URI": database_url,
            }
            first_app = create_app(config)
            with first_app.app_context():
                actor = User(
                    name="Persistent Admin",
                    email="persistent.admin@example.com",
                    role="admin",
                )
                actor.set_password("a-temporary-persistence-password")
                db.session.add(actor)
                db.session.flush()
                event = AuditEvent(
                    actor_id=actor.id,
                    actor_name=actor.name,
                    action="test.audit_persisted",
                    entity_type="user",
                    entity_id=str(actor.id),
                    summary="Synthetic administrative event.",
                )
                db.session.add(event)
                db.session.commit()
                event_id = event.id
                db.session.remove()
                db.engine.dispose()

            restarted_app = create_app(config)
            with restarted_app.app_context():
                persisted = db.session.get(AuditEvent, event_id)
                self.assertIsNotNone(persisted)
                self.assertEqual(persisted.actor_name, "Persistent Admin")
                self.assertEqual(persisted.action, "test.audit_persisted")
                self.assertEqual(persisted.entity_id, str(persisted.actor_id))
                self.assertEqual(persisted.summary, "Synthetic administrative event.")
                db.session.remove()
                db.engine.dispose()

    def test_existing_audit_table_is_migrated_without_losing_events(self):
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy-audit.sqlite"
            connection = sqlite3.connect(database_path)
            connection.execute(
                """
                CREATE TABLE audit_events (
                    id INTEGER PRIMARY KEY,
                    actor_id INTEGER,
                    action VARCHAR(80) NOT NULL,
                    entity_type VARCHAR(40) NOT NULL,
                    entity_id VARCHAR(80) NOT NULL,
                    summary VARCHAR(300) NOT NULL,
                    occurred_at DATETIME NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO audit_events
                    (id, actor_id, action, entity_type, entity_id, summary, occurred_at)
                VALUES (1, NULL, 'legacy.event', 'test', '1', 'Preserved legacy entry',
                        '2026-11-14 12:00:00')
                """
            )
            connection.commit()
            connection.close()

            migrated_app = create_app(
                {
                    "TESTING": True,
                    "SECRET_KEY": "migration-test-key",
                    "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path.as_posix()}",
                }
            )
            with migrated_app.app_context():
                columns = {
                    column["name"] for column in inspect(db.engine).get_columns("audit_events")
                }
                self.assertIn("actor_name", columns)
                event = db.session.get(AuditEvent, 1)
                self.assertEqual(event.action, "legacy.event")
                self.assertEqual(event.summary, "Preserved legacy entry")
                self.assertIsNone(event.actor_name)
                db.session.remove()
                db.engine.dispose()

    def test_patient_and_admin_can_view_printable_invoice_with_ownership_checks(self):
        own_invoice = Invoice(
            patient=self.patient,
            description="Printable consultation",
            amount=Decimal("125.50"),
            status="overdue",
        )
        other_invoice = Invoice(
            patient=self.other_patient,
            description="Another patient's invoice",
            amount=Decimal("75.00"),
        )
        db.session.add_all([own_invoice, other_invoice])
        db.session.commit()
        own_id = own_invoice.id
        other_id = other_invoice.id

        self.sign_in(self.patient)
        own_copy = self.client.get(f"/billing/{own_id}/print")
        self.assertEqual(own_copy.status_code, 200)
        self.assertIn(b"Printable consultation", own_copy.data)
        self.assertIn(b"$125.50", own_copy.data)
        self.assertIn(b"Overdue", own_copy.data)
        self.assertIn(b"data-print", own_copy.data)
        self.assertEqual(self.client.get(f"/billing/{other_id}/print").status_code, 403)
        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})

        self.sign_in(self.doctor)
        self.assertEqual(self.client.get(f"/billing/{own_id}/print").status_code, 403)
        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})

        self.sign_in(self.admin)
        admin_copy = self.client.get(f"/billing/{other_id}/print")
        self.assertEqual(admin_copy.status_code, 200)
        self.assertIn(b"Another patient&#39;s invoice", admin_copy.data)
        self.assertIn(b"View / print", self.client.get("/billing/").data)

    def test_appointment_invoice_rejects_invalid_or_unpriced_appointment(self):
        appointment = self.create_appointment()
        appointment_id = appointment.id
        self.sign_in(self.admin)
        for invalid_appointment_id, message in (
            ("not-an-id", b"Check the patient, amount, and invoice due date"),
            ("0", b"Choose a valid appointment."),
        ):
            response = self.client.post(
                "/admin/invoices/new",
                data={
                    "_csrf_token": self.csrf_token(),
                    "appointment_id": invalid_appointment_id,
                    "patient_id": str(self.patient.id),
                    "description": "Bad appointment",
                    "amount": "50",
                },
                follow_redirects=True,
            )
            self.assertIn(message, response.data)
            self.assertEqual(Invoice.query.count(), 0)
        response = self.client.post(
            "/admin/invoices/new",
            data={
                "_csrf_token": self.csrf_token(),
                "appointment_id": str(appointment_id),
                "patient_id": str(self.patient.id),
            },
            follow_redirects=True,
        )
        self.assertIn(b"no consultation fee configured", response.data)
        self.assertEqual(Invoice.query.count(), 0)

    def test_patient_cannot_create_invoices_and_admin_can_manage_billing(self):
        self.sign_in(self.patient)
        response = self.client.post(
            "/admin/invoices/new", data={"_csrf_token": self.csrf_token()}
        )
        self.assertEqual(response.status_code, 403)
        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})
        self.sign_in(self.admin)
        response = self.client.post(
            "/admin/invoices/new",
            data={
                "_csrf_token": self.csrf_token(),
                "patient_id": str(self.patient.id),
                "description": "Clinic visit",
                "amount": "125.50",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        invoice = Invoice.query.one()
        self.assertEqual(invoice.patient_id, self.patient.id)
        self.assertEqual(str(invoice.amount), "125.50")
        response = self.client.post(
            f"/billing/{invoice.id}/paid",
            data={"_csrf_token": self.csrf_token()},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(invoice.status, "paid")

    def test_admin_can_create_doctor_account_with_licensed_profile(self):
        self.sign_in(self.admin)
        response = self.client.post(
            "/admin/users",
            data={
                "_csrf_token": self.csrf_token(),
                "name": "Doctor Two",
                "email": "doctor.two@example.com",
                "phone": "555-0103",
                "role": "doctor",
                "specialization": "Pediatrics",
                "department": "Children's Health",
                "qualification": "MD, FAAP",
                "experience_years": "9",
                "consultation_fee": "50",
                "license_number": "LIC-002",
                "password": "a-temporary-test-password",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        created = User.query.filter_by(email="doctor.two@example.com").one()
        self.assertEqual(created.doctor_profile.license_number, "LIC-002")
        self.assertEqual(created.doctor_profile.experience_years, 9)
        self.assertEqual(created.doctor_profile.consultation_fee, Decimal("50.00"))

    def test_admin_add_doctor_makes_profile_available_for_booking(self):
        self.sign_in(self.admin)
        response = self.client.post(
            "/admin/doctors",
            data={
                "_csrf_token": self.csrf_token(),
                "name": "Morgan Sample",
                "email": "morgan@example.com",
                "specialization": "Cardiology",
                "qualification": "MD, FACC",
                "experience_years": "12",
                "consultation_fee": "185.00",
                "password": "a-temporary-doctor-password",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        doctor = User.query.filter_by(email="morgan@example.com").one()
        self.assertEqual(doctor.role, "doctor")
        self.assertEqual(doctor.doctor_profile.specialization, "Cardiology")
        self.assertEqual(doctor.doctor_profile.qualification, "MD, FACC")
        self.assertEqual(doctor.doctor_profile.experience_years, 12)
        self.assertEqual(str(doctor.doctor_profile.consultation_fee), "185.00")
        self.assertIn(b"$185.00", response.data)

        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})
        self.sign_in(self.patient)
        response = self.client.get("/appointments/new")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Morgan Sample", response.data)
        self.assertIn(b"MD, FACC", response.data)
        self.assertIn(b"$185.00", response.data)

    def test_admin_can_correct_existing_doctor_without_replacing_the_record(self):
        self.doctor.doctor_profile.experience_years = 0
        self.doctor.doctor_profile.consultation_fee = Decimal("550.00")
        db.session.commit()
        doctor_id = self.doctor.id
        self.sign_in(self.admin)

        page = self.client.get(f"/admin/doctors/{doctor_id}/edit")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'value="0"', page.data)
        self.assertIn(b'value="550.00"', page.data)

        response = self.client.post(
            f"/admin/doctors/{doctor_id}/edit",
            data={
                "_csrf_token": self.csrf_token(),
                "name": self.doctor.name,
                "email": self.doctor.email,
                "specialization": "Family Medicine",
                "qualification": "MD",
                "department": "Primary Care",
                "experience_years": "15",
                "consultation_fee": "50",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"$50.00", response.data)
        self.assertEqual(User.query.filter_by(id=doctor_id).count(), 1)
        self.assertEqual(self.doctor.doctor_profile.experience_years, 15)
        self.assertEqual(self.doctor.doctor_profile.consultation_fee, Decimal("50.00"))

    def test_doctor_form_rejects_fractional_experience_and_overprecision_fee(self):
        self.sign_in(self.admin)
        base_data = {
            "name": "Invalid Doctor",
            "email": "invalid.doctor@example.com",
            "specialization": "Cardiology",
            "qualification": "MD",
            "experience_years": "4",
            "consultation_fee": "50",
            "password": "a-temporary-doctor-password",
        }
        response = self.client.post(
            "/admin/doctors",
            data={
                "_csrf_token": self.csrf_token(),
                **base_data,
                "experience_years": "4.5",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"whole number of experience years", response.data)
        self.assertIsNone(User.query.filter_by(email="invalid.doctor@example.com").first())

        response = self.client.post(
            "/admin/doctors",
            data={
                "_csrf_token": self.csrf_token(),
                **base_data,
                "consultation_fee": "50.001",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"at most two decimal places", response.data)
        self.assertIsNone(User.query.filter_by(email="invalid.doctor@example.com").first())

    def test_appointment_rejects_duplicate_doctor_time_and_invalid_dates(self):
        self.sign_in(self.patient)
        scheduled_at = (datetime.now() + timedelta(days=3)).replace(second=0, microsecond=0)
        form_data = {
            "doctor_id": str(self.doctor.id),
            "scheduled_at": scheduled_at.strftime("%Y-%m-%dT%H:%M"),
            "reason": "Consultation",
        }
        response = self.client.post(
            "/appointments/new",
            data={"_csrf_token": self.csrf_token(), **form_data},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Appointment.query.count(), 1)

        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})
        self.sign_in(self.other_patient)
        response = self.client.post(
            "/appointments/new",
            data={"_csrf_token": self.csrf_token(), **form_data},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"already has an appointment at this time", response.data)
        self.assertEqual(Appointment.query.count(), 1)

        form_data["scheduled_at"] = "not-a-date"
        response = self.client.post(
            "/appointments/new",
            data={"_csrf_token": self.csrf_token(), **form_data},
            follow_redirects=True,
        )
        self.assertIn(b"valid appointment time", response.data)
        self.assertEqual(Appointment.query.count(), 1)

    def test_doctor_can_confirm_and_complete_and_patient_sees_updates(self):
        appointment = self.create_appointment()
        response = self.change_appointment_status(appointment, self.doctor, "confirmed")
        self.assertEqual(response.status_code, 302)
        db.session.refresh(appointment)
        self.assertEqual(appointment.status, "confirmed")

        response = self.client.post(
            f"/appointments/{appointment.id}/status",
            data={"_csrf_token": self.csrf_token(), "status": "completed"},
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(appointment)
        self.assertEqual(appointment.status, "completed")

        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})
        self.sign_in(self.patient)
        response = self.client.get("/appointments/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Completed", response.data)
        self.assertIn(b"No further actions", response.data)

    def test_doctor_can_reject_scheduled_appointment(self):
        appointment = self.create_appointment()
        response = self.change_appointment_status(appointment, self.doctor, "rejected")
        self.assertEqual(response.status_code, 302)
        db.session.refresh(appointment)
        self.assertEqual(appointment.status, "rejected")

    def test_patient_can_cancel_own_scheduled_or_confirmed_appointment(self):
        for starting_status in ("scheduled", "confirmed"):
            with self.subTest(starting_status=starting_status):
                appointment = self.create_appointment(status=starting_status)
                response = self.change_appointment_status(appointment, self.patient, "cancelled")
                self.assertEqual(response.status_code, 302)
                db.session.refresh(appointment)
                self.assertEqual(appointment.status, "cancelled")

    def test_admin_can_make_allowed_transitions_and_view_every_status(self):
        for starting_status, target_status in (
            ("scheduled", "confirmed"),
            ("scheduled", "rejected"),
            ("scheduled", "cancelled"),
            ("confirmed", "completed"),
            ("confirmed", "cancelled"),
        ):
            with self.subTest(starting_status=starting_status, target_status=target_status):
                appointment = self.create_appointment(status=starting_status)
                response = self.change_appointment_status(appointment, self.admin, target_status)
                self.assertEqual(response.status_code, 302)
                db.session.refresh(appointment)
                self.assertEqual(appointment.status, target_status)

        for status in ("scheduled", "confirmed", "rejected", "completed", "cancelled"):
            self.create_appointment(status=status)
        response = self.client.get("/appointments/")
        self.assertEqual(response.status_code, 200)
        for status in ("Scheduled", "Confirmed", "Rejected", "Completed", "Cancelled"):
            with self.subTest(status=status):
                self.assertIn(status.encode(), response.data)

    def test_invalid_status_transitions_are_rejected_without_changing_records(self):
        invalid_attempts = (
            (self.doctor, "scheduled", "completed"),
            (self.doctor, "scheduled", "cancelled"),
            (self.patient, "scheduled", "confirmed"),
            (self.patient, "confirmed", "rejected"),
            (self.admin, "rejected", "confirmed"),
            (self.admin, "completed", "cancelled"),
            (self.admin, "cancelled", "confirmed"),
        )
        for actor, starting_status, target_status in invalid_attempts:
            with self.subTest(role=actor.role, starting_status=starting_status, target_status=target_status):
                appointment = self.create_appointment(status=starting_status)
                response = self.change_appointment_status(appointment, actor, target_status)
                self.assertEqual(response.status_code, 400)
                db.session.refresh(appointment)
                self.assertEqual(appointment.status, starting_status)

    def test_users_cannot_change_appointments_they_do_not_own_or_manage(self):
        appointment = self.create_appointment(patient=self.other_patient)
        self.sign_in(self.patient)
        response = self.client.post(
            f"/appointments/{appointment.id}/status",
            data={"_csrf_token": self.csrf_token(), "status": "cancelled"},
        )
        self.assertEqual(response.status_code, 403)
        db.session.refresh(appointment)
        self.assertEqual(appointment.status, "scheduled")

        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})
        other_doctor = self.make_user("Doctor Two", "doctor.two@example.com", "doctor")
        other_doctor.doctor_profile = DoctorProfile(
            specialization="Neurology",
            department="Neurology",
            license_number="LIC-002",
        )
        db.session.commit()
        self.sign_in(other_doctor)
        response = self.client.post(
            f"/appointments/{appointment.id}/status",
            data={"_csrf_token": self.csrf_token(), "status": "confirmed"},
        )
        self.assertEqual(response.status_code, 403)
        db.session.refresh(appointment)
        self.assertEqual(appointment.status, "scheduled")

    def test_patient_sees_helpful_message_when_no_doctors_exist(self):
        db.session.delete(self.doctor)
        db.session.commit()
        self.sign_in(self.patient)
        response = self.client.get("/appointments/new")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No doctors are available yet", response.data)
        self.assertIn(b"contact the hospital administrator", response.data)

    def test_demo_seed_requires_explicit_opt_in_and_is_idempotent(self):
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=["seed-doctors"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("CARECONNECT_ALLOW_DEMO_SEED=1", result.output)

        with patch.dict(os.environ, {"CARECONNECT_ALLOW_DEMO_SEED": "1"}):
            first = runner.invoke(args=["seed-doctors", "--count", "2"])
            second = runner.invoke(args=["seed-doctors", "--count", "2"])
        self.assertEqual(first.exit_code, 0, first.output)
        self.assertEqual(second.exit_code, 0, second.output)
        self.assertEqual(User.query.filter(User.email.like("demo.doctor%@careconnect.test")).count(), 2)

    def test_user_listing_is_admin_only(self):
        self.sign_in(self.patient)
        self.assertEqual(self.client.get("/admin/users").status_code, 403)
        self.assertEqual(self.client.get("/admin/doctors").status_code, 403)
        self.assertEqual(self.client.get("/admin/").status_code, 403)
        self.assertEqual(self.client.get(f"/admin/doctors/{self.doctor.id}/edit").status_code, 403)
        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})
        self.sign_in(self.admin)
        self.assertEqual(self.client.get("/admin/users").status_code, 200)
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Add a doctor", response.data)

    def test_admin_dashboard_metrics_and_activity_use_database_records(self):
        self.create_appointment(status="scheduled")
        self.create_appointment(status="completed")
        self.create_appointment(status="cancelled")
        db.session.add_all(
            [
                Invoice(patient=self.patient, description="Unpaid invoice", amount=Decimal("40.00")),
                Invoice(patient=self.other_patient, description="Paid invoice", amount=Decimal("50.00"), status="paid"),
            ]
        )
        db.session.add(
            MedicalRecord(
                patient=self.patient,
                doctor=self.doctor,
                diagnosis="Dashboard test diagnosis",
                treatment="Dashboard test treatment",
            )
        )
        db.session.commit()
        self.sign_in(self.admin)

        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        for label in (
            "Total patients",
            "Total doctors",
            "Total appointments",
            "Completed appointments",
            "Pending appointments",
            "Cancelled appointments",
            "Total invoices",
            "Unpaid invoices",
            "RECENT HOSPITAL ACTIVITY",
        ):
            self.assertIn(label.encode(), response.data)
        self.assertIn(b"Clinical notes added for Patient One", response.data)
        for count in (b"<strong>2</strong>", b"<strong>1</strong>", b"<strong>3</strong>"):
            self.assertIn(count, response.data)

    def test_admin_cannot_demote_doctor_and_discard_clinical_profile(self):
        appointment = self.create_appointment()
        doctor_id = self.doctor.id
        self.sign_in(self.admin)

        response = self.client.post(
            f"/admin/users/{doctor_id}/role",
            data={"_csrf_token": self.csrf_token(), "role": "patient"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(db.session.get(User, doctor_id).normalized_role, "doctor")
        self.assertIsNotNone(db.session.get(User, doctor_id).doctor_profile)
        self.assertEqual(db.session.get(Appointment, appointment.id).doctor_id, doctor_id)

    def test_case_insensitive_email_index_prevents_duplicate_accounts(self):
        indexes = {
            index[1]
            for index in db.session.execute(db.text("PRAGMA index_list(users)")).all()
        }
        self.assertIn("uq_users_email_lower", indexes)
        with self.assertRaises(IntegrityError):
            db.session.add(
                User(
                    name="Duplicate Patient",
                    email="PATIENT@example.com",
                    password_hash="not-a-real-password-hash",
                    role="patient",
                )
            )
            db.session.commit()
        db.session.rollback()
        self.assertEqual(User.query.filter(db.func.lower(User.email) == "patient@example.com").count(), 1)

    def test_sqlite_migrations_are_idempotent_when_app_restarts(self):
        with TemporaryDirectory() as directory:
            database_url = f"sqlite:///{(Path(directory) / 'migration.sqlite').as_posix()}"
            config = {
                "TESTING": True,
                "SECRET_KEY": "migration-test-key",
                "SQLALCHEMY_DATABASE_URI": database_url,
            }
            first_app = create_app(config)
            with first_app.app_context():
                db.session.remove()
                db.engine.dispose()

            second_app = create_app(config)
            with second_app.app_context():
                index_names = {
                    row[1]
                    for row in db.session.execute(db.text("PRAGMA index_list(users)")).all()
                }
                self.assertIn("uq_users_email_lower", index_names)
                trigger_names = {
                    row[0]
                    for row in db.session.execute(
                        db.text(
                            "SELECT name FROM sqlite_master "
                            "WHERE type = 'trigger' AND name LIKE 'audit_events_no_%'"
                        )
                    ).all()
                }
                self.assertEqual(
                    trigger_names,
                    {"audit_events_no_delete", "audit_events_no_update"},
                )
                db.session.remove()
                db.engine.dispose()

    def test_admin_can_access_dashboard_doctor_list_and_edit_page(self):
        self.sign_in(self.admin)
        for path in (
            "/admin/",
            "/admin/doctors",
            f"/admin/doctors/{self.doctor.id}/edit",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_admin_role_check_accepts_legacy_case_and_whitespace(self):
        self.admin.role = " Admin "
        db.session.commit()
        self.sign_in(self.admin)
        for path in (
            "/admin/",
            "/admin/doctors",
            f"/admin/doctors/{self.doctor.id}/edit",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_anonymous_user_is_redirected_from_admin_pages(self):
        for path in (
            "/admin/",
            "/admin/doctors",
            f"/admin/doctors/{self.doctor.id}/edit",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/auth/login", response.headers["Location"])

    def test_admin_doctor_form_rejects_malformed_email(self):
        self.sign_in(self.admin)
        response = self.client.post(
            "/admin/doctors",
            data={
                "_csrf_token": self.csrf_token(),
                "name": "Invalid Email Doctor",
                "email": "not-an-email",
                "specialization": "Cardiology",
                "qualification": "MD",
                "experience_years": "4",
                "consultation_fee": "50",
                "password": "a-temporary-doctor-password",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"valid email address", response.data)
        self.assertIsNone(User.query.filter_by(email="not-an-email").first())

    def test_patient_can_update_profile_information(self):
        self.sign_in(self.patient)
        response = self.client.post(
            "/profile",
            data={
                "_csrf_token": self.csrf_token(),
                "name": "Patient Updated",
                "phone": "555-0101",
                "date_of_birth": "1990-04-12",
                "gender": "Woman",
                "blood_group": "O+",
                "address": "10 Care Lane",
                "emergency_contact": "Contact · 555-0102",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Your profile has been updated.", response.data)
        self.assertEqual(self.patient.name, "Patient Updated")
        self.assertEqual(self.patient.patient_profile.blood_group, "O+")

    def test_profile_password_change_validates_current_password_and_updates_hash(self):
        self.sign_in(self.patient)
        original_hash = self.patient.password_hash
        response = self.client.post(
            "/profile",
            data={
                "_csrf_token": self.csrf_token(),
                "name": self.patient.name,
                "phone": "555-0101",
                "current_password": "incorrect-password",
                "new_password": "a-new-secure-password",
                "confirm_password": "a-new-secure-password",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Enter your current password correctly.", response.data)
        self.assertEqual(self.patient.password_hash, original_hash)

        response = self.client.post(
            "/profile",
            data={
                "_csrf_token": self.csrf_token(),
                "name": self.patient.name,
                "phone": "555-0101",
                "current_password": "a-secure-test-password",
                "new_password": "a-new-secure-password",
                "confirm_password": "a-new-secure-password",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Your profile and password have been updated.", response.data)
        self.assertTrue(self.patient.check_password("a-new-secure-password"))
        self.assertNotEqual(self.patient.password_hash, original_hash)

    def test_doctor_can_update_contact_and_availability_without_changing_credentials(self):
        self.sign_in(self.doctor)
        response = self.client.post(
            "/profile",
            data={
                "_csrf_token": self.csrf_token(),
                "name": "Doctor Updated",
                "phone": "555-0123",
                "availability": "Weekdays, 9 AM to 4 PM",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Your profile has been updated.", response.data)
        self.assertEqual(self.doctor.name, "Doctor Updated")
        self.assertEqual(self.doctor.doctor_profile.availability, "Weekdays, 9 AM to 4 PM")
        self.assertEqual(self.doctor.doctor_profile.specialization, "Family Medicine")
        self.assertEqual(self.doctor.doctor_profile.license_number, "LIC-001")

    def test_legacy_patient_role_stays_scoped_to_own_records_and_invoices(self):
        own_invoice = Invoice(patient=self.patient, description="Own visit", amount=Decimal("50.00"))
        other_invoice = Invoice(patient=self.other_patient, description="Private visit", amount=Decimal("75.00"))
        own_record = MedicalRecord(
            patient=self.patient,
            doctor=self.doctor,
            diagnosis="Own diagnosis",
            treatment="Own treatment",
        )
        other_record = MedicalRecord(
            patient=self.other_patient,
            doctor=self.doctor,
            diagnosis="Private diagnosis",
            treatment="Private treatment",
        )
        db.session.add_all([own_invoice, other_invoice, own_record, other_record])
        self.patient.role = " Patient "
        db.session.commit()
        self.sign_in(self.patient)

        invoices_response = self.client.get("/billing/")
        self.assertEqual(invoices_response.status_code, 200)
        self.assertIn(b"Own visit", invoices_response.data)
        self.assertNotIn(b"Private visit", invoices_response.data)
        records_response = self.client.get("/records/")
        self.assertEqual(records_response.status_code, 200)
        self.assertIn(b"Own diagnosis", records_response.data)
        self.assertNotIn(b"Private diagnosis", records_response.data)
        dashboard_response = self.client.get("/dashboard")
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertIn(b"Book an appointment", dashboard_response.data)

    def test_doctor_cannot_access_patient_billing_and_invoice_amount_must_be_valid(self):
        self.sign_in(self.doctor)
        self.assertEqual(self.client.get("/billing/").status_code, 403)
        self.client.post("/auth/logout", data={"_csrf_token": self.csrf_token()})
        self.sign_in(self.admin)
        for invalid_amount in ("NaN", "Infinity", "0", "1.239"):
            with self.subTest(amount=invalid_amount):
                response = self.client.post(
                    "/admin/invoices/new",
                    data={
                        "_csrf_token": self.csrf_token(),
                        "patient_id": str(self.patient.id),
                        "description": "Invalid invoice",
                        "amount": invalid_amount,
                    },
                    follow_redirects=True,
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(Invoice.query.count(), 0)

    def test_sensitive_responses_include_security_headers_and_are_not_cached(self):
        self.sign_in(self.patient)
        response = self.client.get("/records/")
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(response.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")
        self.assertIn("frame-ancestors 'none'", response.headers.get("Content-Security-Policy", ""))
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    def test_login_rejects_external_redirect_target(self):
        response = self.client.post(
            "/auth/login?next=https://example.com",
            data={
                "_csrf_token": self.csrf_token(),
                "email": self.patient.email,
                "password": "a-secure-test-password",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/dashboard"))


if __name__ == "__main__":
    unittest.main()
