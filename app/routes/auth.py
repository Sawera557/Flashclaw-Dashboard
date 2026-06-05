import hashlib
from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from app.services.supabase import supabase, select_one, insert, eq

auth_bp = Blueprint('auth', __name__)

# Password hashing — use SHA-256 with a per-user salt (same as before for compatibility)
# Our demo user in Supabase has: password_hash = SHA-256('flashclaw2026$demo123')

_PEPPER = 'flashclaw2026'


def hash_pw(password):
    return hashlib.sha256(f'{_PEPPER}${password}'.encode()).hexdigest()


def check_pw(stored_hash, password):
    return stored_hash == hash_pw(password)


def user_to_dict(u):
    return {
        'id': u['id'],
        'workspace_id': u['workspace_id'],
        'name': u['name'],
        'email': u['email'],
        'role': u['role'],
        'avatar': u.get('avatar', ''),
    }


@auth_bp.route('/api/auth/register', methods=['POST'])
def register():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    name = data.get('name', '').strip()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    workspace_name = data.get('workspace_name', '').strip()

    if not name or not email or not password:
        return jsonify({'error': 'Name, email, and password are required'}), 400

    # Check if user exists
    existing = select_one('users', filters=[eq('email', email)])
    if existing:
        return jsonify({'error': 'Email already registered'}), 409

    # Create workspace (or use default)
    if not workspace_name:
        workspace_name = f"{name}'s Workspace"
    ws = insert('workspaces', {'name': workspace_name})
    workspace_id = ws.data[0]['id'] if ws.data else 1

    # Create user
    result = insert('users', {
        'workspace_id': workspace_id,
        'name': name,
        'email': email,
        'password_hash': hash_pw(password),
        'role': 'admin',
        'avatar': name[:2].upper() if name else '',
    })

    user_data = result.data[0] if result.data else None
    if not user_data:
        return jsonify({'error': 'Failed to create user'}), 500

    claims = {
        'user_id': user_data['id'],
        'workspace_id': user_data['workspace_id'],
        'role': user_data['role'],
    }
    access_token = create_access_token(identity=str(user_data['id']), additional_claims=claims)

    return jsonify({'access_token': access_token, 'user': user_to_dict(user_data)}), 201


@auth_bp.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400

    user = select_one('users', filters=[eq('email', email)])
    if not user or not check_pw(user.get('password_hash', ''), password):
        return jsonify({'error': 'Invalid credentials'}), 401

    claims = {
        'user_id': user['id'],
        'workspace_id': user['workspace_id'],
        'role': user['role'],
    }
    access_token = create_access_token(identity=str(user['id']), additional_claims=claims)

    return jsonify({'access_token': access_token, 'user': user_to_dict(user)})


@auth_bp.route('/api/auth/refresh', methods=['POST'])
@jwt_required()
def refresh():
    current_user_id = get_jwt_identity()
    user = select_one('users', filters=[eq('id', int(current_user_id))])
    if not user:
        return jsonify({'error': 'User not found'}), 404

    claims = {
        'user_id': user['id'],
        'workspace_id': user['workspace_id'],
        'role': user['role'],
    }
    access_token = create_access_token(identity=str(user['id']), additional_claims=claims)

    return jsonify({'access_token': access_token})
