from pathlib import Path
from uuid import uuid4

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import current_user

from audit import log_audit
from extensions import db
from models import MedicalRecord, MedicalRecordAttachment, User
from utils.decorators import roles_required
from utils.uploads import UploadValidationError, prepare_uploads

records_bp = Blueprint("records", __name__, url_prefix="/records")


@records_bp.get("/")
@roles_required("admin", "doctor", "patient")
def list_records():
    query = MedicalRecord.query
    role = current_user.normalized_role
    if role == "patient":
        query = query.filter_by(patient_id=current_user.id)
    elif role == "doctor":
        query = query.filter_by(doctor_id=current_user.id)
    records = query.order_by(MedicalRecord.recorded_at.desc()).all()
    return render_template("records.html", records=records)


@records_bp.route("/new", methods=["GET", "POST"])
@roles_required("doctor")
def create_record():
    patients = User.query.filter(db.func.lower(db.func.trim(User.role)) == "patient").order_by(User.name).all()
    if request.method == "POST":
        try:
            patient_id = int(request.form.get("patient_id", ""))
        except (ValueError, TypeError):
            abort(400, description="Choose a valid patient.")
        patient = db.session.get(User, patient_id)
        diagnosis = request.form.get("diagnosis", "").strip()
        treatment = request.form.get("treatment", "").strip()
        notes = request.form.get("notes", "").strip()
        if (
            not patient
            or patient.normalized_role != "patient"
            or not diagnosis
            or len(diagnosis) > 300
            or not treatment
            or len(treatment) > 10000
            or len(notes) > 5000
        ):
            flash("Select a patient and provide a diagnosis and treatment.", "error")
        else:
            try:
                attachments = prepare_uploads(request.files.getlist("attachments"))
            except UploadValidationError as error:
                flash(str(error), "error")
            else:
                record = MedicalRecord(
                    patient=patient,
                    doctor_id=current_user.id,
                    diagnosis=diagnosis,
                    treatment=treatment,
                    notes=notes or None,
                )
                _save_record(record, attachments, "medical_record.created", "A medical record was created.")
                flash("Medical record saved.", "success")
                return redirect(url_for("records.list_records"))
    return render_template("record_form.html", patients=patients)


@records_bp.route("/<int:record_id>/edit", methods=["GET", "POST"])
@roles_required("admin", "doctor")
def edit_record(record_id):
    record = db.get_or_404(MedicalRecord, record_id)
    if current_user.normalized_role == "doctor" and record.doctor_id != current_user.id:
        abort(403)
    if request.method == "POST":
        diagnosis = request.form.get("diagnosis", "").strip()
        treatment = request.form.get("treatment", "").strip()
        notes = request.form.get("notes", "").strip()
        if (
            not diagnosis
            or len(diagnosis) > 300
            or not treatment
            or len(treatment) > 10000
            or len(notes) > 5000
        ):
            flash("Provide a diagnosis and treatment within the allowed length limits.", "error")
        else:
            try:
                attachments = prepare_uploads(request.files.getlist("attachments"))
            except UploadValidationError as error:
                flash(str(error), "error")
            else:
                record.diagnosis = diagnosis
                record.treatment = treatment
                record.notes = notes or None
                _save_record(record, attachments, "medical_record.updated", "A medical record was updated.")
                flash("Medical record updated.", "success")
                return redirect(url_for("records.detail", record_id=record.id))
    return render_template("record_form.html", patients=[], record=record)


@records_bp.get("/<int:record_id>")
@roles_required("admin", "doctor", "patient")
def detail(record_id):
    record = db.get_or_404(MedicalRecord, record_id)
    _authorize_record_access(record)
    return render_template("record_detail.html", record=record)


@records_bp.get("/<int:record_id>/attachments/<int:attachment_id>")
@roles_required("admin", "doctor", "patient")
def download_attachment(record_id, attachment_id):
    record = db.get_or_404(MedicalRecord, record_id)
    _authorize_record_access(record)
    attachment = MedicalRecordAttachment.query.filter_by(
        id=attachment_id, medical_record_id=record.id
    ).first_or_404()
    return send_from_directory(
        current_app.config["UPLOAD_FOLDER"],
        attachment.stored_filename,
        as_attachment=True,
        download_name=attachment.original_filename,
        mimetype=attachment.content_type,
        max_age=0,
    )


def _authorize_record_access(record):
    role = current_user.normalized_role
    if role == "patient" and record.patient_id != current_user.id:
        abort(403)
    if role == "doctor" and record.doctor_id != current_user.id:
        abort(403)


def _save_record(record, uploads, action, summary):
    written_paths = []
    committed = False
    try:
        db.session.add(record)
        db.session.flush()
        upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
        upload_folder.mkdir(parents=True, exist_ok=True)
        for upload in uploads:
            stored_filename = f"{uuid4().hex}{upload.extension}"
            destination = upload_folder / stored_filename
            with destination.open("xb") as output:
                written_paths.append(destination)
                output.write(upload.content)
            attachment = MedicalRecordAttachment(
                medical_record=record,
                original_filename=upload.filename,
                stored_filename=stored_filename,
                content_type=upload.content_type,
            )
            db.session.add(attachment)
            db.session.flush()
            log_audit(
                "medical_record.attachment_added",
                "medical_record_attachment",
                attachment.id,
                "A document attachment was added to a medical record.",
            )
        log_audit(action, "medical_record", record.id, summary)
        db.session.commit()
        committed = True
    finally:
        if not committed:
            db.session.rollback()
            for path in written_paths:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    current_app.logger.exception("Unable to remove an uncommitted uploaded attachment.")
