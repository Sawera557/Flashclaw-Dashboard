from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from app import db
from app.models.lead import Lead, LinkedInActivity, GeneratedEmail
from app.models.user import User

activity_bp = Blueprint('activity', __name__)


def _get_current_user(user_id_str):
    try:
        return User.query.get(int(user_id_str))
    except (ValueError, TypeError):
        return None


def _build_daily_breakdown(workspace_id, start_date, end_date):
    """Build per-day activity counts for a given range."""
    days = []
    current = start_date
    while current <= end_date:
        day_start = datetime(current.year, current.month, current.day, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)

        # Emails sent today
        emails_sent = GeneratedEmail.query.join(
            Lead, GeneratedEmail.lead_id == Lead.id
        ).filter(
            Lead.workspace_id == workspace_id,
            GeneratedEmail.sent == True,
            GeneratedEmail.sent_at >= day_start,
            GeneratedEmail.sent_at < day_end,
        ).count()

        # LinkedIn activities today
        li_activities = LinkedInActivity.query.filter_by(
            workspace_id=workspace_id
        ).filter(
            LinkedInActivity.created_at >= day_start,
            LinkedInActivity.created_at < day_end,
        )

        connections_sent = li_activities.filter(
            LinkedInActivity.activity_type == 'connection_request'
        ).count()

        replies = li_activities.filter(
            LinkedInActivity.activity_type.in_(['reply_received', 'positive_reply'])
        ).count()

        meetings = li_activities.filter(
            LinkedInActivity.activity_type == 'meeting_booked'
        ).count()

        positive_replies = li_activities.filter(
            LinkedInActivity.activity_type.in_(['positive_reply', 'interested'])
        ).count()

        days.append({
            'date': day_start.strftime('%Y-%m-%d'),
            'emails_sent': emails_sent,
            'connections_sent': connections_sent,
            'replies': replies,
            'meetings': meetings,
            'positive_replies': positive_replies,
        })

        current += timedelta(days=1)

    return days


@activity_bp.route('/api/activity', methods=['GET'])
@jwt_required()
def get_activity():
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    period = request.args.get('period', 'daily')  # daily, weekly, monthly
    workspace_id = user.workspace_id
    now = datetime.now(timezone.utc)
    today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    if period == 'daily':
        start_date = today
    elif period == 'monthly':
        start_date = today - timedelta(days=29)
    else:  # weekly (default)
        start_date = today - timedelta(days=6)

    end_date = now

    # Totals for the period
    emails_sent = GeneratedEmail.query.join(
        Lead, GeneratedEmail.lead_id == Lead.id
    ).filter(
        Lead.workspace_id == workspace_id,
        GeneratedEmail.sent == True,
        GeneratedEmail.sent_at >= start_date,
    ).count()

    li_all = LinkedInActivity.query.filter_by(
        workspace_id=workspace_id
    ).filter(
        LinkedInActivity.created_at >= start_date,
    )

    connections_sent = li_all.filter(
        LinkedInActivity.activity_type == 'connection_request'
    ).count()

    replies = li_all.filter(
        LinkedInActivity.activity_type.in_(['reply_received', 'positive_reply'])
    ).count()

    meetings = li_all.filter(
        LinkedInActivity.activity_type == 'meeting_booked'
    ).count()

    positive_replies = li_all.filter(
        LinkedInActivity.activity_type.in_(['positive_reply', 'interested'])
    ).count()

    # Daily breakdown for charts
    daily_breakdown = _build_daily_breakdown(workspace_id, start_date, end_date)

    return jsonify({
        'period': period,
        'totals': {
            'emails_sent': emails_sent,
            'connections_sent': connections_sent,
            'replies': replies,
            'meetings': meetings,
            'positive_replies': positive_replies,
        },
        'daily_breakdown': daily_breakdown,
    })
