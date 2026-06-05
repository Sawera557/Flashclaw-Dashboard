import time

from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.services.supabase import supabase, select, select_one, eq

admin_bp = Blueprint('admin', __name__)

_start_time = time.time()


def _get_current_user(user_id_str):
    try:
        return select_one('users', filters=[eq('id', int(user_id_str))])
    except (ValueError, TypeError):
        return None


@admin_bp.route('/api/admin/users', methods=['GET'])
@jwt_required()
def list_users():
    current_user_id = get_jwt_identity()
    current_user = _get_current_user(current_user_id)
    if not current_user:
        return jsonify({'error': 'User not found'}), 404

    if current_user.get('role') not in ('admin', 'manager'):
        return jsonify({'error': 'Admin access required'}), 403

    users_result = supabase.table('users').select('*').eq('workspace_id', current_user['workspace_id']).execute()
    users = users_result.data

    return jsonify({
        'users': [{
            'id': u['id'],
            'workspace_id': u['workspace_id'],
            'name': u['name'],
            'email': u['email'],
            'role': u['role'],
            'avatar': u.get('avatar', ''),
            'created_at': u.get('created_at'),
        } for u in users],
        'total': len(users),
    })


@admin_bp.route('/api/admin/system-health', methods=['GET'])
def system_health():
    """Public healthcheck endpoint. No auth required."""
    try:
        supabase.table('users').select('id').limit(1).execute()
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
