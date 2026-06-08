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


@activity_bp.route('/api/activity/timeframe', methods=['GET'])
@jwt_required()
def get_activity_timeframe():
    """
    Activity analytics for a configurable time range.
    ?days=7 | 30 | 90 | 180 | all
    Returns per-day breakdown + aggregated totals + metrics breakdown.
    """
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    workspace_id = user['workspace_id']
    now = datetime.now(timezone.utc)
    today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    days_str = request.args.get('days', '30')
    if days_str == 'all':
        start_date = today - timedelta(days=365*5)  # far enough back
    else:
        try:
            days_num = int(days_str)
        except ValueError:
            days_num = 30
        start_date = today - timedelta(days=days_num)

    end_date = now
    start_date_iso = start_date.isoformat()

    # Lead totals across entire workspace
    total_leads = 0
    try:
        total_res = supabase.table('leads').select('id', count='exact').eq('workspace_id', workspace_id).execute()
        total_leads = int(total_res.count) if hasattr(total_res, 'count') else len(total_res.data)
    except Exception:
        pass

    # Leads created in period
    new_leads = 0
    try:
        new_res = supabase.table('leads').select('id', count='exact').eq('workspace_id', workspace_id).gte('created_at', start_date_iso).execute()
        new_leads = int(new_res.count) if hasattr(new_res, 'count') else len(new_res.data)
    except Exception:
        pass

    # Leads with meetings booked
    meetings_booked = 0
    try:
        mtg_res = supabase.table('meetings').select('id', count='exact').eq('workspace_id', workspace_id).gte('created_at', start_date_iso).execute()
        meetings_booked = int(mtg_res.count) if hasattr(mtg_res, 'count') else len(mtg_res.data)
    except Exception:
        pass

    # Enriched leads in period
    enriched = 0
    try:
        enr_res = supabase.table('leads').select('id', count='exact').eq('workspace_id', workspace_id).gte('enriched_at', start_date_iso).execute()
        enriched = int(enr_res.count) if hasattr(enr_res, 'count') else len(enr_res.data)
    except Exception:
        pass

    # Email stats
    emails_sent = 0
    email_replies = 0
    ws_lead_ids = []
    leads_res = supabase.table('leads').select('id').eq('workspace_id', workspace_id).execute()
    ws_lead_ids = [l['id'] for l in leads_res.data]

    if ws_lead_ids:
        try:
            es = supabase.table('email_activities').select('id', count='exact').in_('lead_id', ws_lead_ids).eq('status', 'sent').execute()
            emails_sent = int(es.count) if hasattr(es, 'count') else len(es.data)
        except Exception:
            pass
        try:
            er = supabase.table('email_activities').select('id', count='exact').in_('lead_id', ws_lead_ids).eq('status', 'replied').execute()
            email_replies = int(er.count) if hasattr(er, 'count') else len(er.data)
        except Exception:
            pass

    # LinkedIn totals in period
    connections_result = supabase.table('linkedin_activities').select('id', count='exact').eq('workspace_id', workspace_id).eq('activity_type', 'connection_request').gte('created_at', start_date_iso).execute()
    connections_sent = int(connections_result.count) if hasattr(connections_result, 'count') else len(connections_result.data)

    dms_result = supabase.table('linkedin_activities').select('id', count='exact').eq('workspace_id', workspace_id).eq('activity_type', 'dm_sent').gte('created_at', start_date_iso).execute()
    dms_sent = int(dms_result.count) if hasattr(dms_result, 'count') else len(dms_result.data)

    replies_result = supabase.table('linkedin_activities').select('id', count='exact').eq('workspace_id', workspace_id).in_('activity_type', ['reply_received', 'positive_reply']).gte('created_at', start_date_iso).execute()
    li_replies = int(replies_result.count) if hasattr(replies_result, 'count') else len(replies_result.data)

    positive_result = supabase.table('linkedin_activities').select('id', count='exact').eq('workspace_id', workspace_id).in_('activity_type', ['positive_reply', 'interested']).gte('created_at', start_date_iso).execute()
    positive_replies = int(positive_result.count) if hasattr(positive_result, 'count') else len(positive_result.data)

    # Daily breakdown for charts
    daily_breakdown = _build_daily_breakdown(workspace_id, start_date, end_date)

    # Conversion metrics
    conversion_rate = round(meetings_booked / total_leads * 100, 1) if total_leads > 0 else 0.0
    reply_rate = round((email_replies + li_replies) / max(emails_sent + connections_sent + dms_sent, 1) * 100, 1)

    return jsonify({
        'period_days': days_str,
        'totals': {
            'emails_sent': emails_sent,
            'connections_sent': connections_sent,
            'dms_sent': dms_sent,
            'replies': email_replies + li_replies,
            'meetings_booked': meetings_booked,
            'positive_replies': positive_replies,
            'total_leads': total_leads,
            'new_leads': new_leads,
            'enriched_leads': enriched,
        },
        'rates': {
            'conversion_rate': conversion_rate,
            'reply_rate': reply_rate,
            'enrichment_rate': round(enriched / max(total_leads, 1) * 100, 1),
        },
        'daily_breakdown': daily_breakdown,
    })


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
