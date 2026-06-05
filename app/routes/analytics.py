from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.services.supabase import supabase, select, select_one, eq, in_

analytics_bp = Blueprint('analytics', __name__)


def _get_current_user(user_id_str):
    try:
        return select_one('users', filters=[eq('id', int(user_id_str))])
    except (ValueError, TypeError):
        return None


@analytics_bp.route('/api/analytics/pipeline', methods=['GET'])
@jwt_required()
def pipeline_analytics():
    current_user_id = get_jwt_identity()
    user = _get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    # Leads count
    leads_result = supabase.table('leads').select('id', count='exact').eq('workspace_id', user['workspace_id']).execute()
    leads_found = int(leads_result.count) if hasattr(leads_result, 'count') else len(leads_result.data)

    # Generated emails — workspace-aware
    leads_in_ws = supabase.table('leads').select('id').eq('workspace_id', user['workspace_id']).execute()
    ws_lead_ids = [l['id'] for l in leads_in_ws.data]

    emails = []
    emails_sent = 0
    emails_generated = 0
    try:
        if ws_lead_ids:
            gen_emails_result = supabase.table('generated_emails').select('sent').in_('lead_id', ws_lead_ids).execute()
            emails = gen_emails_result.data
            emails_sent = sum(1 for e in emails if e.get('sent'))
            emails_generated = len(emails)
    except Exception:
        # generated_emails table may not exist yet
        pass

    # LinkedIn activities
    activities_result = supabase.table('linkedin_activities').select('activity_type').eq('workspace_id', user['workspace_id']).execute()
    activities = activities_result.data
    replies = sum(1 for a in activities if a.get('activity_type') == 'reply_received')
    meetings = sum(1 for a in activities if a.get('activity_type') == 'meeting_booked')
    interested = sum(1 for a in activities if a.get('activity_type') == 'interested')

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

    # Fetch all leads for workspace
    leads_result = supabase.table('leads').select('source,lead_score').eq('workspace_id', user['workspace_id']).execute()
    leads = leads_result.data

    # Group by source
    source_data = {}
    for lead in leads:
        source = lead.get('source') or 'unknown'
        if source not in source_data:
            source_data[source] = {
                'source': source,
                'leads': 0,
                'total_score': 0,
                'reply_count': 0,
            }
        source_data[source]['leads'] += 1
        source_data[source]['total_score'] += float(lead.get('lead_score') or 0)

    # LinkedIn activities per source
    activities_result = supabase.table('linkedin_activities').select('source,activity_type').eq('workspace_id', user['workspace_id']).execute()
    activity_source_data = {}
    for a in activities_result.data:
        src = a.get('source') or 'manual'
        if src not in activity_source_data:
            activity_source_data[src] = {'replies': 0}
        if a.get('activity_type') in ('reply_received', 'interested'):
            activity_source_data[src]['replies'] += 1

    result = []
    for source, data in source_data.items():
        avg_score = round(data['total_score'] / data['leads'], 1) if data['leads'] > 0 else 0
        reply_count = activity_source_data.get(source, {}).get('replies', 0)
        reply_rate = round(reply_count / data['leads'] * 100, 1) if data['leads'] > 0 else 0

        result.append({
            'source': source,
            'leads': data['leads'],
            'reply_rate': reply_rate,
            'quality_score': avg_score,
        })

    result.sort(key=lambda x: x['leads'], reverse=True)

    return jsonify(result)
