from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from app import db
from app.models.lead import Lead, LinkedInActivity, GeneratedEmail
from app.models.user import User

analytics_bp = Blueprint('analytics', __name__)


def _get_current_user(user_id_str):
    try:
        return User.query.get(int(user_id_str))
    except (ValueError, TypeError):
        return None


@analytics_bp.route('/api/analytics/pipeline', methods=['GET'])
@jwt_required()
def pipeline_analytics():
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    leads = Lead.query.filter_by(workspace_id=user.workspace_id).all()
    emails = GeneratedEmail.query.join(
        Lead, GeneratedEmail.lead_id == Lead.id
    ).filter(
        Lead.workspace_id == user.workspace_id
    ).all()

    leads_found = len(leads)
    emails_sent = sum(1 for e in emails if e.sent)
    emails_generated = len(emails)

    # Activities-based metrics
    activities = LinkedInActivity.query.filter_by(workspace_id=user.workspace_id).all()
    replies = sum(1 for a in activities if a.activity_type == 'reply_received')
    meetings = sum(1 for a in activities if a.activity_type == 'meeting_booked')
    interested = sum(1 for a in activities if a.activity_type == 'interested')

    reply_rate = round(replies / emails_sent * 100, 1) if emails_sent > 0 else 0
    conversion_rate = round(meetings / leads_found * 100, 1) if leads_found > 0 else 0

    return jsonify({
        'leads_found': leads_found,
        'emails_generated': emails_generated,
        'emails_sent': emails_sent,
        'replies': replies,
        'meetings': meetings,
        'interested': interested,
        'reply_rate': reply_rate,
        'conversion_rate': conversion_rate,
    })


@analytics_bp.route('/api/analytics/by-source', methods=['GET'])
@jwt_required()
def analytics_by_source():
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    leads = Lead.query.filter_by(workspace_id=user.workspace_id).all()

    # Group by source
    source_data = {}
    for lead in leads:
        source = lead.source or 'unknown'
        if source not in source_data:
            source_data[source] = {
                'source': source,
                'leads': 0,
                'total_score': 0,
                'reply_count': 0,
            }
        source_data[source]['leads'] += 1
        source_data[source]['total_score'] += lead.lead_score

    # Get activities per source — we can approximate by joining
    activities = LinkedInActivity.query.filter_by(workspace_id=user.workspace_id).all()
    activity_source_data = {}
    for a in activities:
        src = a.source or 'manual'
        if src not in activity_source_data:
            activity_source_data[src] = {'replies': 0}
        if a.activity_type == 'reply_received' or a.activity_type == 'interested':
            activity_source_data[src]['replies'] += 1

    result = []
    for source, data in source_data.items():
        avg_score = round(data['total_score'] / data['leads'], 1) if data['leads'] > 0 else 0
        reply_count = activity_source_data.get(source, {}).get('replies', 0)
        reply_rate = round(reply_count / leads_total(data, source) * 100, 1) if data['leads'] > 0 else 0

        result.append({
            'source': source,
            'leads': data['leads'],
            'reply_rate': reply_rate,
            'quality_score': avg_score,
        })

    result.sort(key=lambda x: x['leads'], reverse=True)

    return jsonify(result)


def leads_total(data_dict, source):
    """Helper to get total leads for reply rate calculation."""
    return data_dict.get('leads', 1)
