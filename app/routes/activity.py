from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.services.supabase import supabase, select, select_one, eq, in_, gte, lte

activity_bp = Blueprint('activity', __name__)


def _get_current_user(user_id_str):
    try:
        return select_one('users', filters=[eq('id', int(user_id_str))])
    except (ValueError, TypeError):
        return None


def _build_daily_breakdown(workspace_id, start_date, end_date):
    """Build per-day activity counts for a given range."""
    days = []
    current = start_date
    while current <= end_date:
        day_start = datetime(current.year, current.month, current.day, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        day_start_iso = day_start.isoformat()
        day_end_iso = day_end.isoformat()

        # Generated emails sent today
        leads_in_ws = supabase.table('leads').select('id').eq('workspace_id', workspace_id).execute()
        ws_lead_ids = [l['id'] for l in leads_in_ws.data]

        emails_sent = 0
        if ws_lead_ids:
            try:
                emails_result = supabase.table('generated_emails').select('id', count='exact').in_('lead_id', ws_lead_ids).eq('sent', True).gte('sent_at', day_start_iso).lt('sent_at', day_end_iso).execute()
                emails_sent = int(emails_result.count) if hasattr(emails_result, 'count') else len(emails_result.data)
            except Exception:
                pass

        # LinkedIn activities today
        connections_result = supabase.table('linkedin_activities').select('id', count='exact').eq('workspace_id', workspace_id).eq('activity_type', 'connection_request').gte('created_at', day_start_iso).lt('created_at', day_end_iso).execute()
        connections_sent = int(connections_result.count) if hasattr(connections_result, 'count') else len(connections_result.data)

        replies_result = supabase.table('linkedin_activities').select('id', count='exact').eq('workspace_id', workspace_id).in_('activity_type', ['reply_received', 'positive_reply']).gte('created_at', day_start_iso).lt('created_at', day_end_iso).execute()
        replies = int(replies_result.count) if hasattr(replies_result, 'count') else len(replies_result.data)

        meetings_result = supabase.table('linkedin_activities').select('id', count='exact').eq('workspace_id', workspace_id).eq('activity_type', 'meeting_booked').gte('created_at', day_start_iso).lt('created_at', day_end_iso).execute()
        meetings = int(meetings_result.count) if hasattr(meetings_result, 'count') else len(meetings_result.data)

        positive_replies_result = supabase.table('linkedin_activities').select('id', count='exact').eq('workspace_id', workspace_id).in_('activity_type', ['positive_reply', 'interested']).gte('created_at', day_start_iso).lt('created_at', day_end_iso).execute()
        positive_replies = int(positive_replies_result.count) if hasattr(positive_replies_result, 'count') else len(positive_replies_result.data)

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
    workspace_id = user['workspace_id']
    now = datetime.now(timezone.utc)
    today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    if period == 'daily':
        start_date = today
    elif period == 'monthly':
        start_date = today - timedelta(days=29)
    else:  # weekly (default)
        start_date = today - timedelta(days=6)

    end_date = now
    start_date_iso = start_date.isoformat()

    # Totals for the period
    leads_in_ws = supabase.table('leads').select('id').eq('workspace_id', workspace_id).execute()
    ws_lead_ids = [l['id'] for l in leads_in_ws.data]

    emails_sent = 0
    if ws_lead_ids:
        try:
            emails_result = supabase.table('generated_emails').select('id', count='exact').in_('lead_id', ws_lead_ids).eq('sent', True).gte('sent_at', start_date_iso).execute()
            emails_sent = int(emails_result.count) if hasattr(emails_result, 'count') else len(emails_result.data)
        except Exception:
            pass

    connections_result = supabase.table('linkedin_activities').select('id', count='exact').eq('workspace_id', workspace_id).eq('activity_type', 'connection_request').gte('created_at', start_date_iso).execute()
    connections_sent = int(connections_result.count) if hasattr(connections_result, 'count') else len(connections_result.data)

    replies_result = supabase.table('linkedin_activities').select('id', count='exact').eq('workspace_id', workspace_id).in_('activity_type', ['reply_received', 'positive_reply']).gte('created_at', start_date_iso).execute()
    replies = int(replies_result.count) if hasattr(replies_result, 'count') else len(replies_result.data)

    meetings_result = supabase.table('linkedin_activities').select('id', count='exact').eq('workspace_id', workspace_id).eq('activity_type', 'meeting_booked').gte('created_at', start_date_iso).execute()
    meetings = int(meetings_result.count) if hasattr(meetings_result, 'count') else len(meetings_result.data)

    positive_replies_result = supabase.table('linkedin_activities').select('id', count='exact').eq('workspace_id', workspace_id).in_('activity_type', ['positive_reply', 'interested']).gte('created_at', start_date_iso).execute()
    positive_replies = int(positive_replies_result.count) if hasattr(positive_replies_result, 'count') else len(positive_replies_result.data)

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
