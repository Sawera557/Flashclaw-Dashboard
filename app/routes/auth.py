from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from app import db
from app.models.user import User, Workspace

auth_bp = Blueprint('auth', __name__)


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

    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'Email already registered'}), 409

    # Create workspace (or use default)
    if not workspace_name:
        workspace_name = f"{name}'s Workspace"
    workspace = Workspace(name=workspace_name)
    db.session.add(workspace)
    db.session.flush()

    # Create user
    user = User(
        workspace_id=workspace.id,
        name=name,
        email=email,
        role='admin',
        avatar=name[:2].upper() if name else '',
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    access_token = create_access_token(
        identity=str(user.id),
        additional_claims={
            'user_id': user.id,
            'workspace_id': user.workspace_id,
            'role': user.role,
        }
    )

    return jsonify({
        'access_token': access_token,
        'user': user.to_dict(),
    }), 201


@auth_bp.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({'error': 'Invalid credentials'}), 401

    access_token = create_access_token(
        identity=str(user.id),
        additional_claims={
            'user_id': user.id,
            'workspace_id': user.workspace_id,
            'role': user.role,
        }
    )

    return jsonify({
        'access_token': access_token,
        'user': user.to_dict(),
    })


@auth_bp.route('/api/auth/refresh', methods=['POST'])
@jwt_required()
def refresh():
    current_user_id = get_jwt_identity()
    from app.models.user import User
    user = User.query.get(int(current_user_id))
    if not user:
        return jsonify({'error': 'User not found'}), 404

    access_token = create_access_token(
        identity=str(user.id),
        additional_claims={
            'user_id': user.id,
            'workspace_id': user.workspace_id,
            'role': user.role,
        }
    )

    return jsonify({'access_token': access_token})
