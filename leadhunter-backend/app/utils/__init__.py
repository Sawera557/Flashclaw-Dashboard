from functools import wraps
from flask import jsonify
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request


def require_auth(f):
    """Decorator that requires a valid JWT and passes current user info."""
    @wraps(f)
    def decorated(*args, **kwargs):
        verify_jwt_in_request()
        current_user = get_jwt_identity()
        return f(current_user, *args, **kwargs)
    return decorated
