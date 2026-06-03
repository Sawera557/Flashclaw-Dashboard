import time

from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from app import db
from app.models.user import User
from app.models.lead import Lead, LinkedInActivity, GeneratedEmail

admin_bp = Blueprint('admin', __name__)

_start_time = time.time()


def _get_current_user(user_id_str):
    try:
        return User.query.get(int(user_id_str))
    except (ValueError, TypeError):
        return None


@admin_bp.route('/api/admin/users', methods=['GET'])
@jwt_required()
def list_users():
    current_user_id = get_jwt_identity()
    current_user = _get_current_user(current_user_id)
    if not current_user:
        return jsonify({'error': 'User not found'}), 404

    if current_user.role not in ('admin', 'manager'):
        return jsonify({'error': 'Admin access required'}), 403

    users = User.query.filter_by(workspace_id=current_user.workspace_id).all()

    return jsonify({
        'users': [user.to_dict() for user in users],
        'total': len(users),
    })


@admin_bp.route('/api/admin/system-health', methods=['GET'])
def system_health():
    """Public healthcheck endpoint. No auth required."""
    try:
        # Verify DB connection
        db.session.execute(db.text('SELECT 1'))
        db_ok = True
    except Exception:
        db_ok = False

    uptime_seconds = int(time.time() - _start_time)

    return jsonify({
        'status': 'healthy' if db_ok else 'degraded',
        'db': 'connected' if db_ok else 'error',
        'uptime': uptime_seconds,
        'uptime_human': f'{uptime_seconds // 3600}h {(uptime_seconds % 3600) // 60}m',
    })
