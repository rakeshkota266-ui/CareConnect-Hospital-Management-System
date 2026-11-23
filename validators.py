import re


EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
PHONE_PATTERN = re.compile(r"^[0-9+().\-\s]+$")


def valid_email(email):
    if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
        return False
    local_part = email.rsplit("@", 1)[0]
    return not local_part.startswith(".") and not local_part.endswith(".") and ".." not in local_part


def valid_phone(phone):
    return not phone or (
        len(phone) <= 30
        and PHONE_PATTERN.fullmatch(phone) is not None
        and sum(char.isdigit() for char in phone) >= 7
    )
