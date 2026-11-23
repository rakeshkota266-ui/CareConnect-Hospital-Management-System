from functools import wraps

from flask import abort
from flask_login import current_user, login_required


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            user_role = current_user.normalized_role
            allowed_roles = {role.strip().casefold() for role in roles}
            if user_role not in allowed_roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator
