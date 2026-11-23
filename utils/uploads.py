from dataclasses import dataclass
from pathlib import Path

from werkzeug.utils import secure_filename


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_FILE_TYPES = {
    ".pdf": ("application/pdf", lambda data: data.startswith(b"%PDF-")),
    ".png": ("image/png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    ".jpg": ("image/jpeg", lambda data: data.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", lambda data: data.startswith(b"\xff\xd8\xff")),
}


class UploadValidationError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedUpload:
    filename: str
    content_type: str
    extension: str
    content: bytes


def prepare_uploads(files):
    prepared = []
    total_size = 0
    for uploaded_file in files:
        if not uploaded_file or not uploaded_file.filename:
            continue

        filename = secure_filename(uploaded_file.filename)
        if not filename or len(filename) > 255:
            raise UploadValidationError("Use a valid file name no longer than 255 characters.")

        extension = Path(filename).suffix.casefold()
        file_type = ALLOWED_FILE_TYPES.get(extension)
        if file_type is None:
            raise UploadValidationError("Attachments must be PDF, PNG, or JPEG files.")

        content = uploaded_file.stream.read(MAX_UPLOAD_BYTES + 1)
        uploaded_file.stream.seek(0)
        if not content:
            raise UploadValidationError("Uploaded attachments cannot be empty.")
        if len(content) > MAX_UPLOAD_BYTES:
            raise UploadValidationError("Each attachment must be 10 MB or smaller.")

        total_size += len(content)
        if total_size > MAX_UPLOAD_BYTES:
            raise UploadValidationError("Attachments must total 10 MB or less per request.")

        content_type, signature_check = file_type
        if not signature_check(content):
            raise UploadValidationError("The file contents do not match the selected file type.")

        prepared.append(
            PreparedUpload(
                filename=filename,
                content_type=content_type,
                extension=extension,
                content=content,
            )
        )
    return prepared
