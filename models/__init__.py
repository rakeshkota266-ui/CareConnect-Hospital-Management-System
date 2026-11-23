from datetime import datetime, timezone
from decimal import Decimal

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db, login_manager


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="patient", index=True)
    phone = db.Column(db.String(30))
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    patient_profile = db.relationship(
        "PatientProfile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    doctor_profile = db.relationship(
        "DoctorProfile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def normalized_role(self):
        return (self.role or "").strip().casefold()

    @property
    def initials(self):
        return "".join(part[0] for part in self.name.split()[:2]).upper()


class PatientProfile(db.Model):
    __tablename__ = "patient_profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    date_of_birth = db.Column(db.Date)
    gender = db.Column(db.String(30))
    blood_group = db.Column(db.String(5))
    address = db.Column(db.String(300))
    emergency_contact = db.Column(db.String(120))

    user = db.relationship("User", back_populates="patient_profile")


class DoctorProfile(db.Model):
    __tablename__ = "doctor_profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    specialization = db.Column(db.String(120), nullable=False)
    department = db.Column(db.String(120), nullable=False)
    license_number = db.Column(db.String(80), unique=True, nullable=False)
    qualification = db.Column(db.String(160), nullable=False, default="")
    experience_years = db.Column(db.Integer, nullable=False, default=0)
    consultation_fee = db.Column(db.Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    availability = db.Column(db.String(200), default="Contact reception for availability")

    user = db.relationship("User", back_populates="doctor_profile")


class Appointment(db.Model):
    __tablename__ = "appointments"
    STATUSES = ("scheduled", "confirmed", "rejected", "completed", "cancelled")
    __table_args__ = (
        db.UniqueConstraint("doctor_id", "scheduled_at", name="uq_appointments_doctor_scheduled_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    scheduled_at = db.Column(db.DateTime, nullable=False, index=True)
    reason = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="scheduled", index=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    patient = db.relationship("User", foreign_keys=[patient_id], backref="patient_appointments")
    doctor = db.relationship("User", foreign_keys=[doctor_id], backref="doctor_appointments")


class MedicalRecord(db.Model):
    __tablename__ = "medical_records"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey("appointments.id"))
    diagnosis = db.Column(db.String(300), nullable=False)
    treatment = db.Column(db.Text, nullable=False)
    notes = db.Column(db.Text)
    recorded_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True
    )

    patient = db.relationship("User", foreign_keys=[patient_id], backref="medical_records")
    doctor = db.relationship("User", foreign_keys=[doctor_id], backref="authored_records")
    appointment = db.relationship("Appointment", backref="medical_records")
    attachments = db.relationship(
        "MedicalRecordAttachment",
        back_populates="medical_record",
        cascade="all, delete-orphan",
        order_by="MedicalRecordAttachment.created_at",
    )


class MedicalRecordAttachment(db.Model):
    __tablename__ = "medical_record_attachments"

    id = db.Column(db.Integer, primary_key=True)
    medical_record_id = db.Column(
        db.Integer, db.ForeignKey("medical_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(80), nullable=False, unique=True)
    content_type = db.Column(db.String(80), nullable=False)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    medical_record = db.relationship("MedicalRecord", back_populates="attachments")


class Invoice(db.Model):
    __tablename__ = "invoices"
    STATUSES = ("unpaid", "paid", "overdue", "cancelled")

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey("appointments.id"))
    description = db.Column(db.String(300), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="unpaid", index=True)
    issued_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    due_date = db.Column(db.Date)

    patient = db.relationship("User", backref="invoices")
    appointment = db.relationship("Appointment", backref="invoices")
    payment_simulation = db.relationship(
        "DemoPaymentSimulation", back_populates="invoice", uselist=False, cascade="all, delete-orphan"
    )


class AuditEvent(db.Model):
    __tablename__ = "audit_events"
    __table_args__ = (
        db.Index("ix_audit_events_occurred_at", "occurred_at"),
        db.Index("ix_audit_events_entity", "entity_type", "entity_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    actor_name = db.Column(db.String(120))
    action = db.Column(db.String(80), nullable=False, index=True)
    entity_type = db.Column(db.String(40), nullable=False)
    entity_id = db.Column(db.String(80), nullable=False)
    summary = db.Column(db.String(300), nullable=False)
    occurred_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    actor = db.relationship("User", backref="audit_events")


class DemoPaymentSimulation(db.Model):
    __tablename__ = "demo_payment_simulations"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(
        db.Integer, db.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    patient_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    reference = db.Column(db.String(40), nullable=False, unique=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    invoice = db.relationship("Invoice", back_populates="payment_simulation")
    patient = db.relationship("User", backref="demo_payment_simulations")


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None
