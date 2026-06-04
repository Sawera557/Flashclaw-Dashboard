from datetime import datetime, timezone

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from app import db
from app.models.lead import LinkedInActivity
from app.models.user import User

linkedin_bp = Blueprint('linkedin', __name__)


def _get_current_user(user_id_str):
    try:
        return User.query.get(int(user_id_str))
    except (ValueError, TypeError):
        return None


@linkedin_bp.route('/api/linkedin/activities', methods=['GET'])
@jwt_required()
def list_activities():
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    activity_type = request.args.get('type', '', type=str)
    source = request.args.get('source', '', type=str)

    per_page = min(per_page, 100)
    query = LinkedInActivity.query.filter_by(workspace_id=user.workspace_id)

    if activity_type:
        query = query.filter(LinkedInActivity.activity_type == activity_type)

    if source:
        query = query.filter(LinkedInActivity.source == source)

    query = query.order_by(LinkedInActivity.created_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        'activities': [a.to_dict() for a in pagination.items],
        'total': pagination.total,
        'page': page,
        'pages': pagination.pages,
    })


@linkedin_bp.route('/api/linkedin/activities', methods=['POST'])
@jwt_required()
def create_activity():
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    activity = LinkedInActivity(
        workspace_id=user.workspace_id,
        user_id=user.id,
        lead_name=data.get('lead_name', ''),
        company=data.get('company', ''),
        linkedin_url=data.get('linkedin_url', ''),
        activity_type=data.get('activity_type', 'dm_sent'),
        activity_date=data.get('activity_date', ''),
        notes=data.get('notes', ''),
        source=data.get('source', 'manual'),
    )

    db.session.add(activity)
    db.session.commit()

    return jsonify({'activity': activity.to_dict()}), 201


@linkedin_bp.route('/api/linkedin/activities/<int:activity_id>', methods=['PUT'])
@jwt_required()
def update_activity(activity_id):
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    activity = LinkedInActivity.query.filter_by(
        id=activity_id, workspace_id=user.workspace_id
    ).first()

    if not activity:
        return jsonify({'error': 'Activity not found'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    allowed_fields = [
        'lead_name', 'company', 'linkedin_url', 'activity_type',
        'activity_date', 'notes', 'source',
    ]

    for field in allowed_fields:
        if field in data:
            setattr(activity, field, data[field])

    db.session.commit()

    return jsonify({'activity': activity.to_dict()})


@linkedin_bp.route('/api/linkedin/activities/<int:activity_id>', methods=['DELETE'])
@jwt_required()
def delete_activity(activity_id):
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    activity = LinkedInActivity.query.filter_by(
        id=activity_id, workspace_id=user.workspace_id
    ).first()

    if not activity:
        return jsonify({'error': 'Activity not found'}), 404

    db.session.delete(activity)
    db.session.commit()

    return jsonify({'success': True})


@linkedin_bp.route('/api/linkedin/activities/batch-delete', methods=['POST'])
@jwt_required()
def batch_delete_activities():
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json() or {}
    ids = data.get('ids', [])
    if not ids:
        return jsonify({'error': 'No IDs provided'}), 400

    deleted = LinkedInActivity.query.filter(
        LinkedInActivity.id.in_(ids),
        LinkedInActivity.workspace_id == user.workspace_id
    ).delete(synchronize_session=False)
    db.session.commit()

    return jsonify({'success': True, 'deleted': deleted})


@linkedin_bp.route('/api/linkedin/stats', methods=['GET'])
@jwt_required()
def get_linkedin_stats():
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    activities = LinkedInActivity.query.filter_by(
        workspace_id=user.workspace_id
    ).all()

    total = len(activities)
    accepted = sum(1 for a in activities if a.activity_type == 'connection_accepted')
    dms = sum(1 for a in activities if a.activity_type == 'dm_sent')
    replies = sum(1 for a in activities if a.activity_type == 'reply_received')
    meetings = sum(1 for a in activities if a.activity_type == 'meeting_booked')
    interested = sum(1 for a in activities if a.activity_type == 'interested')
    connections_sent = sum(1 for a in activities if a.activity_type == 'connection_sent')

    return jsonify({
        'total': total,
        'connections_sent': connections_sent,
        'accepted': accepted,
        'dms': dms,
        'replies': replies,
        'meetings': meetings,
        'interested': interested,
        'accept_rate': round(accepted / connections_sent * 100, 1) if connections_sent > 0 else 0,
        'reply_rate': round(replies / dms * 100, 1) if dms > 0 else 0,
        'meeting_rate': round(meetings / (replies + dms) * 100, 1) if (replies + dms) > 0 else 0,
    })
