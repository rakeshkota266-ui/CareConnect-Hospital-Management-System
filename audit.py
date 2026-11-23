from flask import has_request_context
from flask_login import current_user

from extensions import db
from models import AuditEvent


def log_audit(action, entity_type, entity_id, summary, actor_id=None, *, system=False):
    if actor_id is None and not system and has_request_context() and current_user.is_authenticated:
        actor_id = current_user.id
    actor_name = current_user.name if has_request_context() and current_user.is_authenticated and actor_id == current_user.id else None
    event = AuditEvent(
        actor_id=actor_id,
        actor_name=actor_name,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        summary=summary,
    )
    db.session.add(event)
    return event
