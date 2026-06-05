"""Dashboard API — aggregated SDR command center data.

GET /api/dashboard/summary — returns KPI stats, SDR profile, today queue,
activity timeline, source performance, pipeline snapshot, AI recommendations.
All data is workspace-aware and user-aware via JWT.
"""

import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.services.supabase import supabase, select, select_one, insert, update, delete, eq, gte, lte, in_
from app.services.maton_calendar import get_events

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint('dashboard', __name__)


def _get_user(user_id_str):
    try:
        return select_one('users', filters=[eq('id', int(user_id_str))])
    except (ValueError, TypeError):
        return None


# ── Helpers ─────────────────────────────────────────────────────────


def _today_start():
    """Start of today in UTC."""
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _today_end():
    return datetime.now(timezone.utc).replace(hour=23, minute=59, second=59, microsecond=999999)


def _days_ago(n):
    return datetime.now(timezone.utc) - timedelta(days=n)


def _fmt_date(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if hasattr(dt, 'isoformat'):
        return dt.isoformat()
    return str(dt)


# ── Maton Calendar Meetings ──────────────────────────────────────────


def _build_maton_meetings(workspace_id):
    """Fetch upcoming meetings from Google Calendar via Maton."""
    try:
        data = get_events(days_back=3, days_ahead=7, max_results=50)
        # Map to a simple format for the dashboard
        meetings = []
        for m in data.get('upcoming', []):
            meetings.append({
                'id': m.get('id', ''),
                'title': m.get('title', ''),
                'start': m.get('start', ''),
                'end': m.get('end', ''),
                'company': m.get('company', ''),
                'client_name': m.get('client_name', ''),
                'location': m.get('location', ''),
                'conference_link': m.get('conference_link', ''),
                'organizer': m.get('organizer', ''),
                'attendees': m.get('attendees', []),
                'attendee_count': len(m.get('attendees', [])),
            })
        return meetings
    except Exception as e:
        logger.warning(f'Maton meetings unavailable: {e}')
        return []


# ── Stats builder ────────────────────────────────────────────────────


def _build_stats(workspace_id, user_id):
    """KPI calculations — total, new, enriched, emails, replies, meetings, pipeline value, conversion."""
    today_start_iso = _today_start().isoformat()
    today_end_iso = _today_end().isoformat()

    # Lead counts
    total_leads = 0
    new_today = 0
    enriched = 0
    try:
        total_leads_result = supabase.table('leads').select('id', count='exact').eq('workspace_id', workspace_id).execute()
        total_leads = int(total_leads_result.count) if hasattr(total_leads_result, 'count') else len(total_leads_result.data)

        new_today_result = supabase.table('leads').select('id', count='exact').eq('workspace_id', workspace_id).gte('created_at', today_start_iso).lte('created_at', today_end_iso).execute()
        new_today = int(new_today_result.count) if hasattr(new_today_result, 'count') else len(new_today_result.data)

        enriched_result = supabase.table('leads').select('id', count='exact').eq('workspace_id', workspace_id).not_.is_('enriched_at', 'null').execute()
        enriched = int(enriched_result.count) if hasattr(enriched_result, 'count') else len(enriched_result.data)
    except Exception:
        logger.warning('leads table not available, using 0 counts')

    # Email stats (from EmailActivity table — table may not exist)
    emails_sent = 0
    try:
        emails_sent_result = supabase.table('email_activities').select('id', count='exact').eq('workspace_id', workspace_id).eq('status', 'sent').execute()
        emails_sent = int(emails_sent_result.count) if hasattr(emails_sent_result, 'count') else len(emails_sent_result.data)
    except Exception:
        pass

    replies_received = 0
    try:
        replies_received_result = supabase.table('email_activities').select('id', count='exact').eq('workspace_id', workspace_id).eq('status', 'replied').execute()
        replies_received = int(replies_received_result.count) if hasattr(replies_received_result, 'count') else len(replies_received_result.data)
    except Exception:
        pass

    positive_replies = 0
    try:
        positive_replies_result = supabase.table('email_activities').select('id', count='exact').eq('workspace_id', workspace_id).eq('reply_sentiment', 'positive').execute()
        positive_replies = int(positive_replies_result.count) if hasattr(positive_replies_result, 'count') else len(positive_replies_result.data)
    except Exception:
        pass

    # Fallback to LinkedInActivity if no email_activities exist yet
    if replies_received == 0:
        try:
            legacy_replies_result = supabase.table('linkedin_activities').select('id', count='exact').eq('workspace_id', workspace_id).in_('activity_type', ['reply_received', 'interested']).execute()
            legacy_replies = int(legacy_replies_result.count) if hasattr(legacy_replies_result, 'count') else len(legacy_replies_result.data)
            replies_received = max(replies_received, legacy_replies)
        except Exception:
            pass

        if positive_replies == 0:
            try:
                legacy_positive_result = supabase.table('linkedin_activities').select('id', count='exact').eq('workspace_id', workspace_id).eq('activity_type', 'interested').execute()
                positive_replies = int(legacy_positive_result.count) if hasattr(legacy_positive_result, 'count') else len(legacy_positive_result.data)
            except Exception:
                pass

    # Meetings (table may not exist — wrap in try/except)
    meetings_booked = 0
    meetings_completed = 0
    try:
        meetings_booked_result = supabase.table('meetings').select('id', count='exact').eq('workspace_id', workspace_id).eq('status', 'scheduled').execute()
        meetings_booked = int(meetings_booked_result.count) if hasattr(meetings_booked_result, 'count') else len(meetings_booked_result.data)
    except Exception as e:
        logger.warning(f'meetings table not available (booked): {e}')

    try:
        meetings_completed_result = supabase.table('meetings').select('id', count='exact').eq('workspace_id', workspace_id).eq('status', 'completed').execute()
        meetings_completed = int(meetings_completed_result.count) if hasattr(meetings_completed_result, 'count') else len(meetings_completed_result.data)
    except Exception as e:
        logger.warning(f'meetings table not available (completed): {e}')

    total_meetings = meetings_booked + meetings_completed

    # Pipeline value from HubspotDeal cache
    pipeline_value = 0
    try:
        deals_result = supabase.table('hubspot_deals').select('amount').eq('workspace_id', workspace_id).execute()
        pipeline_deals = deals_result.data
        pipeline_value = sum(float(d.get('amount') or 0) for d in pipeline_deals)
    except Exception:
        logger.warning('hubspot_deals table not available, using 0 pipeline value')

    # Conversion rate: total meetings / total leads
    conversion_rate = round(total_meetings / total_leads * 100, 1) if total_leads > 0 else 0.0
    reply_rate = round(replies_received / emails_sent * 100, 1) if emails_sent > 0 else 0.0

    return {
        'total_leads': total_leads,
        'new_today': new_today,
        'enriched_leads': enriched,
        'emails_sent': emails_sent,
        'replies_received': replies_received,
        'positive_replies': positive_replies,
        'meetings_booked': total_meetings,
        'pipeline_value': pipeline_value,
        'conversion_rate': conversion_rate,
        'reply_rate': reply_rate,
    }


# ── SDR Profile ──────────────────────────────────────────────────────


def _build_sdr_profile(user):
    """SDR identity card data — name, role, team, streak, rank."""
    workspace_id = user['workspace_id']

    # Streak: consecutive days with activity
    today_start = _today_start()
    streak = 0
    check_date = datetime.now(timezone.utc).date()
    for days_back in range(0, 60):
        day_start = datetime(check_date.year, check_date.month, check_date.day, tzinfo=timezone.utc) - timedelta(days=days_back)
        day_end = day_start + timedelta(hours=23, minutes=59, seconds=59)
        day_start_iso = day_start.isoformat()
        day_end_iso = day_end.isoformat()

        li_result = supabase.table('linkedin_activities').select('id').eq('workspace_id', workspace_id).eq('user_id', user['id']).gte('created_at', day_start_iso).lte('created_at', day_end_iso).limit(1).execute()
        had_li = len(li_result.data) > 0

        email_result = supabase.table('email_activities').select('id').eq('workspace_id', workspace_id).eq('user_id', user['id']).gte('created_at', day_start_iso).lte('created_at', day_end_iso).limit(1).execute()
        had_email = len(email_result.data) > 0

        if had_li or had_email:
            streak += 1
        elif days_back > 0:
            break

    # Rank based on total leads generated in workspace
    user_leads_result = supabase.table('leads').select('id', count='exact').eq('workspace_id', workspace_id).eq('user_id', user['id']).execute()
    user_leads = int(user_leads_result.count) if hasattr(user_leads_result, 'count') else len(user_leads_result.data)

    total_workspace_leads_result = supabase.table('leads').select('id', count='exact').eq('workspace_id', workspace_id).execute()
    total_workspace_leads = int(total_workspace_leads_result.count) if hasattr(total_workspace_leads_result, 'count') else len(total_workspace_leads_result.data)

    if user_leads >= 50:
        rank = 'Prospector 🏆'
    elif user_leads >= 20:
        rank = 'Hunter 💪'
    elif user_leads >= 10:
        rank = 'Scout 🎯'
    elif user_leads >= 1:
        rank = 'Rookie 🚀'
    else:
        rank = 'New Recruit 🌱'

    # Team members (same workspace, role=sdr)
    team_result = supabase.table('users').select('id', count='exact').eq('workspace_id', workspace_id).eq('role', 'sdr').execute()
    team_members = int(team_result.count) if hasattr(team_result, 'count') else len(team_result.data)

    return {
        'name': user['name'],
        'email': user['email'],
        'role': user['role'].capitalize(),
        'avatar': user.get('avatar') or user['name'][:2].upper(),
        'team_size': team_members + 1,
        'streak': streak,
        'rank': rank,
        'leads_generated': user_leads,
    }


# ── Today's Work Queue ──────────────────────────────────────────────


def _build_today_queue(workspace_id, user_id):
    """Follow-ups due, positive replies needing response, meetings today, leads needing enrichment."""
    today_start_iso = _today_start().isoformat()
    today_end_iso = _today_end().isoformat()

    # Follow-ups due: leads with status 'contacted' or 'replied'
    follow_ups_result = supabase.table('leads').select('id', count='exact').eq('workspace_id', workspace_id).eq('user_id', user_id).in_('status', ['contacted', 'replied']).execute()
    follow_ups_due = int(follow_ups_result.count) if hasattr(follow_ups_result, 'count') else len(follow_ups_result.data)

    # Positive replies needing response
    positive_emails_result = supabase.table('email_activities').select('*').eq('workspace_id', workspace_id).eq('user_id', user_id).eq('reply_sentiment', 'positive').not_.is_('replied_at', 'null').execute()
    positive_emails = positive_emails_result.data

    needs_response = 0
    for email in positive_emails:
        replied_at = email.get('replied_at')
        # Look for a sent email after this reply
        if replied_at:
            responded_result = supabase.table('email_activities').select('id').eq('workspace_id', workspace_id).eq('user_id', user_id).eq('lead_id', email.get('lead_id')).eq('status', 'sent').gt('sent_at', replied_at).limit(1).execute()
            if len(responded_result.data) == 0:
                needs_response += 1

    # Also check LinkedInActivity for interested
    interested_result = supabase.table('linkedin_activities').select('id').eq('workspace_id', workspace_id).eq('user_id', user_id).eq('activity_type', 'interested').execute()
    needs_response += len(interested_result.data)

    # Meetings today — from DB and Maton Calendar
    meetings_today_db = 0
    try:
        meetings_today_result = supabase.table('meetings').select('id', count='exact').eq('workspace_id', workspace_id).eq('user_id', user_id).eq('status', 'scheduled').gte('scheduled_at', today_start_iso).lte('scheduled_at', today_end_iso).execute()
        meetings_today_db = int(meetings_today_result.count) if hasattr(meetings_today_result, 'count') else len(meetings_today_result.data)
    except Exception:
        pass

    # Also count from Maton Calendar (with timeout)
    meetings_today_maton = 0
    import threading as _mt
    _mt_result = []
    def _fetch_mtg_today():
        try:
            _d = get_events(days_back=0, days_ahead=1, max_results=50)
            _mt_result.append(_d)
        except Exception:
            pass
    _t = _mt.Thread(target=_fetch_mtg_today)
    _t.start()
    _t.join(timeout=5)
    if _mt_result:
        for m in _mt_result[0].get('upcoming', []):
            if m.get('start', '').startswith(today_start_iso[:10]):
                meetings_today_maton += 1

    meetings_today = max(meetings_today_db, meetings_today_maton)
    # Leads needing enrichment (no enriched_at, score < 50)
    needs_enrichment_result = supabase.table('leads').select('id', count='exact').eq('workspace_id', workspace_id).is_('enriched_at', 'null').lt('lead_score', 50).execute()
    needs_enrichment = int(needs_enrichment_result.count) if hasattr(needs_enrichment_result, 'count') else len(needs_enrichment_result.data)

    return {
        'follow_ups_due': follow_ups_due,
        'positive_replies_needing_response': needs_response,
        'meetings_today': meetings_today,
        'leads_needing_enrichment': needs_enrichment,
    }


# ── Activity Timeline ───────────────────────────────────────────────


def _build_recent_activities(workspace_id, user_id, limit=15):
    """Combined timeline of recent activities across all activity tables."""
    entries = []

    # Lead activities
    lead_acts_result = supabase.table('lead_activities').select('*').eq('workspace_id', workspace_id).eq('user_id', user_id).order('created_at', desc=True).limit(limit).execute()
    for a in lead_acts_result.data:
        entries.append({
            'type': a.get('activity_type', ''),
            'description': a.get('description', ''),
            'timestamp': _fmt_date(a.get('created_at')),
            'icon': '📋',
        })

    # Email activities
    email_acts_result = supabase.table('email_activities').select('*').eq('workspace_id', workspace_id).eq('user_id', user_id).order('created_at', desc=True).limit(limit).execute()
    icon_map = {'sent': '📤', 'opened': '👁️', 'clicked': '🖱️', 'replied': '💬', 'bounced': '❌'}
    for e in email_acts_result.data:
        entries.append({
            'type': f"email_{e.get('status', '')}",
            'description': f"{e.get('status', '').capitalize()}: {e.get('subject', '')[:80]} → {e.get('recipient', '')}",
            'timestamp': _fmt_date(e.get('sent_at') or e.get('created_at')),
            'icon': icon_map.get(e.get('status', ''), '📧'),
        })

    # LinkedIn activities
    li_acts_result = supabase.table('linkedin_activities').select('*').eq('workspace_id', workspace_id).eq('user_id', user_id).order('created_at', desc=True).limit(limit).execute()
    icon_li_map = {
        'connection_sent': '🔗', 'connection_accepted': '✅',
        'dm_sent': '💌', 'reply_received': '📬',
        'interested': '👍', 'not_interested': '👎',
        'meeting_booked': '📅', 'followup_sent': '🔄',
        'profile_viewed': '👀',
    }
    for a in li_acts_result.data:
        desc = a.get('notes') or f"{a.get('activity_type', '').replace('_', ' ').title()} — {a.get('lead_name', '')}"
        entries.append({
            'type': f"linkedin_{a.get('activity_type', '')}",
            'description': desc[:120],
            'timestamp': _fmt_date(a.get('created_at')),
            'icon': icon_li_map.get(a.get('activity_type', ''), '🔗'),
        })

    # Meetings
    meetings_result = supabase.table('meetings').select('*').eq('workspace_id', workspace_id).eq('user_id', user_id).order('created_at', desc=True).limit(limit).execute()
    status_icon = {'scheduled': '📅', 'completed': '✅', 'cancelled': '❌', 'no_show': '🚫'}
    for m in meetings_result.data:
        desc = f"Meeting {m.get('status', '')}: {m.get('title') or m.get('meeting_type', '')}"
        entries.append({
            'type': f"meeting_{m.get('status', '')}",
            'description': desc,
            'timestamp': _fmt_date(m.get('scheduled_at') or m.get('created_at')),
            'icon': status_icon.get(m.get('status', ''), '📅'),
        })

    # Sort by timestamp desc, take top limit
    entries.sort(key=lambda x: x.get('timestamp') or '', reverse=True)
    return entries[:limit]


# ── Source Performance ──────────────────────────────────────────────


def _build_source_performance(workspace_id):
    """Lead source performance: volume, reply rate, quality score per source."""
    # Fetch all leads for workspace, grouped by source in Python
    leads_result = supabase.table('leads').select('id,source,lead_score,first_name,company').eq('workspace_id', workspace_id).execute()
    all_source_leads = leads_result.data

    # Group by source
    source_groups = {}
    for l in all_source_leads:
        src = l.get('source') or 'unknown'
        if src == '':
            src = 'unknown'
        if src not in source_groups:
            source_groups[src] = {'lead_ids': [], 'scores': [], 'count': 0}
        source_groups[src]['lead_ids'].append(l['id'])
        source_groups[src]['scores'].append(float(l.get('lead_score') or 0))
        source_groups[src]['count'] += 1

    results = []
    for source_name, group in source_groups.items():
        lead_count = group['count']
        avg_score = round(sum(group['scores']) / lead_count, 1) if lead_count > 0 else 0.0

        # Count replies for leads from this source
        email_replies = 0
        if group['lead_ids']:
            email_replies_result = supabase.table('email_activities').select('id', count='exact').in_('lead_id', group['lead_ids']).eq('status', 'replied').execute()
            email_replies = int(email_replies_result.count) if hasattr(email_replies_result, 'count') else len(email_replies_result.data)

            # Also check LinkedIn activity
            li_replies = 0
            for l in group['lead_ids']:
                # Find linkedin_activities with matching lead_name
                lead_row = next((x for x in all_source_leads if x['id'] == l), {})
                lead_first = (lead_row.get('first_name') or '')
                lead_company = (lead_row.get('company') or '')
            # Batch check — approximate by workspace+source matching
            li_replies_result = supabase.table('linkedin_activities').select('id', count='exact').eq('workspace_id', workspace_id).in_('activity_type', ['reply_received', 'interested']).execute()
            li_replies = int(li_replies_result.count) if hasattr(li_replies_result, 'count') else len(li_replies_result.data)

            email_replies = max(email_replies, li_replies)

        reply_rate = round(email_replies / lead_count * 100, 1) if lead_count > 0 else 0.0

        results.append({
            'source': source_name.capitalize(),
            'leads': lead_count,
            'reply_rate': reply_rate,
            'quality_score': avg_score,
        })

    results.sort(key=lambda x: x['leads'], reverse=True)
    return results


# ── Pipeline Snapshot ───────────────────────────────────────────────


def _build_pipeline_snapshot(workspace_id):
    """Pipeline breakdown by stage."""
    # Try HubspotDeal cache first
    deals = []
    try:
        deals_result = supabase.table('hubspot_deals').select('*').eq('workspace_id', workspace_id).execute()
        deals = deals_result.data
    except Exception:
        logger.warning('hubspot_deals table not available, using lead statuses')

    if deals:
        stage_map = {}
        for d in deals:
            s = d.get('stage') or 'Unknown'
            if s not in stage_map:
                stage_map[s] = {'count': 0, 'value': 0.0}
            stage_map[s]['count'] += 1
            stage_map[s]['value'] += float(d.get('amount') or 0)

        stages = [
            {
                'stage': stage,
                'count': data['count'],
                'value': data['value'],
            }
            for stage, data in stage_map.items()
        ]
        stages.sort(key=lambda x: x['value'], reverse=True)
        return {
            'source': 'hubspot',
            'total_deals': len(deals),
            'total_value': sum(float(d.get('amount') or 0) for d in deals),
            'stages': stages,
        }

    # Fallback: build from lead statuses
    status_result = supabase.table('leads').select('status').eq('workspace_id', workspace_id).execute()
    status_counts = {}
    for l in status_result.data:
        s = l.get('status') or 'new'
        status_counts[s] = status_counts.get(s, 0) + 1

    stages = [
        {
            'stage': s.capitalize(),
            'count': count,
            'value': 0,
        }
        for s, count in status_counts.items()
    ]
    stages.sort(key=lambda x: x['count'], reverse=True)

    return {
        'source': 'leads',
        'total_deals': sum(s['count'] for s in stages),
        'total_value': 0,
        'stages': stages,
    }


# ── AI Recommendations ──────────────────────────────────────────────


def _build_ai_recommendations(workspace_id, user_id, stats, queue):
    """Generate smart recommendations based on dashboard data + AI context."""
    recommendations = []

    # 1) High activity but low reply rate → improve messaging
    if stats['emails_sent'] > 10 and stats['reply_rate'] < 10:
        recommendations.append({
            'action': 'Improve email messaging',
            'reason': f'Reply rate is only {stats["reply_rate"]}%. Consider A/B testing subject lines or personalizing deeper.',
            'impact': 'High',
            'type': 'messaging',
        })

    # 2) New leads without enrichment
    if queue['leads_needing_enrichment'] > 5:
        recommendations.append({
            'action': 'Enrich un-scored leads',
            'reason': f'{queue["leads_needing_enrichment"]} leads need enrichment. Scored leads convert 2x better.',
            'impact': 'High',
            'type': 'enrichment',
        })

    # 3) Positive replies not responded to
    if queue['positive_replies_needing_response'] > 0:
        recommendations.append({
            'action': 'Respond to hot leads',
            'reason': f'{queue["positive_replies_needing_response"]} positive replies need follow-up. Respond within 24hr for best conversion.',
            'impact': 'High',
            'type': 'response',
        })

    # 4) Low pipeline value
    if stats['pipeline_value'] < 10000 and stats['total_leads'] > 20:
        recommendations.append({
            'action': 'Focus on top leads',
            'reason': 'Pipeline value is low despite sufficient leads. Prioritize highest-scored leads for outreach.',
            'impact': 'Medium',
            'type': 'pipeline',
        })

    # 5) Heavy reliance on one source
    sources_data = _build_source_performance(workspace_id)
    if sources_data and len(sources_data) >= 1:
        top_source = sources_data[0]
        total_lead_count = sum(s['leads'] for s in sources_data)
        if total_lead_count > 0 and (top_source['leads'] / total_lead_count) > 0.8:
            recommendations.append({
                'action': 'Diversify lead sources',
                'reason': f"{top_source['source']} provides {round(top_source['leads']/total_lead_count*100)}% of leads. Add another source to reduce risk.",
                'impact': 'Medium',
                'type': 'sourcing',
            })

    # 6) No activity today
    today_start_iso = _today_start().isoformat()
    today_li_result = supabase.table('linkedin_activities').select('id').eq('workspace_id', workspace_id).eq('user_id', user_id).gte('created_at', today_start_iso).execute()
    today_li = len(today_li_result.data)

    today_email_result = supabase.table('email_activities').select('id').eq('workspace_id', workspace_id).eq('user_id', user_id).gte('created_at', today_start_iso).execute()
    today_email = len(today_email_result.data)

    if today_li == 0 and today_email == 0:
        recommendations.append({
            'action': 'Start your outreach',
            'reason': 'No activity recorded today. Send at least 5 connection requests or cold emails to keep momentum.',
            'impact': 'Medium',
            'type': 'activity',
        })

    # 7) Streak-based encouragement
    if stats['meetings_booked'] > 0 and stats['conversion_rate'] > 0:
        recommendations.append({
            'action': 'Leverage your wins',
            'reason': f"You've booked {stats['meetings_booked']} meetings at {stats['conversion_rate']}% conversion. Ask happy leads for referrals!",
            'impact': 'Low',
            'type': 'referral',
        })

    # If no recommendations generated, give general guidance
    if not recommendations:
        recommendations.append({
            'action': 'Keep building pipeline',
            'reason': 'Everything looks balanced. Continue consistent outreach and look for ways to personalize at scale.',
            'impact': 'Low',
            'type': 'general',
        })

    return recommendations


# ── Seed Demo Dashboard Data ─────────────────────────────────────────


def _seed_dashboard_data(user):
    """Seed demo meeting and email activity data if none exists."""
    workspace_id = user['workspace_id']

    try:
        existing_meetings = supabase.table('meetings').select('id').eq('workspace_id', workspace_id).limit(1).execute()
        if existing_meetings.data:
            return  # Already seeded
    except Exception:
        pass  # Table may not exist, that's OK

    # Fetch leads
    leads_result = supabase.table('leads').select('*').eq('workspace_id', workspace_id).execute()
    leads = leads_result.data
    if not leads:
        return

    now = datetime.now(timezone.utc)

    # Demo meetings
    demo_meetings = []

    if len(leads) > 4:
        demo_meetings.append({
            'lead_id': leads[4]['id'],
            'title': 'Demo: CloudSecure AI platform',
            'meeting_type': 'demo',
            'scheduled_at': (now + timedelta(hours=3)).isoformat(),
            'duration_minutes': 30,
            'status': 'scheduled',
        })
    else:
        demo_meetings.append({
            'lead_id': leads[0]['id'],
            'title': 'Demo: CloudSecure AI platform',
            'meeting_type': 'demo',
            'scheduled_at': (now + timedelta(hours=3)).isoformat(),
            'duration_minutes': 30,
            'status': 'scheduled',
        })

    if len(leads) > 3:
        demo_meetings.append({
            'lead_id': leads[3]['id'],
            'title': 'Discovery call — Ecommerce Scale',
            'meeting_type': 'discovery',
            'scheduled_at': (now + timedelta(days=1, hours=10)).isoformat(),
            'duration_minutes': 45,
            'status': 'scheduled',
        })
    else:
        demo_meetings.append({
            'lead_id': leads[0]['id'],
            'title': 'Discovery call — Ecommerce Scale',
            'meeting_type': 'discovery',
            'scheduled_at': (now + timedelta(days=1, hours=10)).isoformat(),
            'duration_minutes': 45,
            'status': 'scheduled',
        })

    if len(leads) > 2:
        demo_meetings.append({
            'lead_id': leads[2]['id'],
            'title': 'Follow-up: HealthTech AI',
            'meeting_type': 'followup',
            'scheduled_at': (now - timedelta(days=2)).isoformat(),
            'duration_minutes': 30,
            'status': 'completed',
            'notes': 'Interested in pilot program. Follow up next week.',
        })
    else:
        demo_meetings.append({
            'lead_id': leads[0]['id'],
            'title': 'Follow-up: HealthTech AI',
            'meeting_type': 'followup',
            'scheduled_at': (now - timedelta(days=2)).isoformat(),
            'duration_minutes': 30,
            'status': 'completed',
            'notes': 'Interested in pilot program. Follow up next week.',
        })

    for m in demo_meetings:
        m['workspace_id'] = workspace_id
        m['user_id'] = user['id']

    for m in demo_meetings:
        try:
            insert('meetings', m)
        except Exception as e:
            logger.warning(f'Could not seed meeting: {e}')

    # Demo email activities
    demo_emails = []

    lead0 = leads[0]
    demo_emails.append({
        'lead_id': lead0['id'],
        'subject': 'Quick question about TechStartup.io',
        'recipient': 'sarah.chen@techstartup.io',
        'status': 'sent',
        'sent_at': (now - timedelta(days=5)).isoformat(),
        'email_type': 'cold',
    })
    demo_emails.append({
        'lead_id': lead0['id'],
        'subject': 'Re: Quick question about TechStartup.io',
        'recipient': 'sarah.chen@techstartup.io',
        'status': 'replied',
        'sent_at': (now - timedelta(days=4)).isoformat(),
        'replied_at': (now - timedelta(days=4, hours=6)).isoformat(),
        'reply_sentiment': 'neutral',
        'email_type': 'followup',
    })

    if len(leads) > 3:
        demo_emails.append({
            'lead_id': leads[3]['id'],
            'subject': 'Growth opportunities for Ecommerce Scale',
            'recipient': 'jwilson@ecommercescale.com',
            'status': 'replied',
            'sent_at': (now - timedelta(days=2)).isoformat(),
            'replied_at': (now - timedelta(days=1)).isoformat(),
            'reply_sentiment': 'positive',
            'email_type': 'cold',
        })

    if len(leads) > 4:
        demo_emails.append({
            'lead_id': leads[4]['id'],
            'subject': 'Security solutions for CloudSecure',
            'recipient': 'aisha@cloudsecure.dev',
            'status': 'sent',
            'sent_at': (now - timedelta(hours=12)).isoformat(),
            'email_type': 'cold',
        })

    if len(leads) > 1:
        demo_emails.append({
            'lead_id': leads[1]['id'],
            'subject': 'Fintech Pro — partnership opportunity',
            'recipient': 'marcus@fintechpro.com',
            'status': 'sent',
            'sent_at': (now - timedelta(hours=6)).isoformat(),
            'email_type': 'cold',
        })

    for e in demo_emails:
        e['workspace_id'] = workspace_id
        e['user_id'] = user['id']

    for e in demo_emails:
        try:
            insert('email_activities', e)
        except Exception as e_err:
            logger.warning(f'Could not seed email activity: {e_err}')

    # Demo lead activities
    import random
    demo_lead_activities = []

    demo_lead_activities.append({
        'lead_id': leads[0]['id'],
        'activity_type': 'enrichment',
        'description': 'Enriched Sarah Chen — added company size and industry',
    })
    demo_lead_activities.append({
        'lead_id': leads[0]['id'],
        'activity_type': 'score',
        'description': 'Scored Sarah Chen at 85 — strong ICP match',
    })
    if len(leads) > 4:
        demo_lead_activities.append({
            'lead_id': leads[4]['id'],
            'activity_type': 'status_change',
            'description': 'Changed Aisha Patel status to meeting_booked',
        })

    for a in demo_lead_activities:
        a['workspace_id'] = workspace_id
        a['user_id'] = user['id']
        a['created_at'] = (now - timedelta(hours=random.randint(1, 72))).isoformat()
        insert('lead_activities', a)

    logger.info('Seeded demo dashboard data')


# ── Main Endpoint ────────────────────────────────────────────────────


@dashboard_bp.route('/api/dashboard/summary', methods=['GET'])
@jwt_required()
def dashboard_summary():
    """Main dashboard summary endpoint — all data in one response."""
    current_user_id = get_jwt_identity()
    user = _get_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    workspace_id = user['workspace_id']

    # Seed demo data on first run if empty
    try:
        _seed_dashboard_data(user)
    except Exception:
        pass

    # Build each section independently so one failure doesn't crash everything
    stats = {}
    sdr_profile = {}
    today_queue = {}
    recent_activities = []
    source_performance = []
    pipeline_snapshot = {}
    ai_recommendations = []
    maton_meetings = []

    for fname, fargs in [
        ('stats', lambda: _build_stats(workspace_id, user['id'])),
        ('sdr_profile', lambda: _build_sdr_profile(user)),
        ('today_queue', lambda: _build_today_queue(workspace_id, user['id'])),
        ('recent_activities', lambda: _build_recent_activities(workspace_id, user['id'])),
        ('source_performance', lambda: _build_source_performance(workspace_id)),
        ('pipeline_snapshot', lambda: _build_pipeline_snapshot(workspace_id)),
        ('ai_recommendations', lambda: _build_ai_recommendations(workspace_id, user['id'], stats, today_queue)),
    ]:
        try:
            result = fargs()
            if fname == 'stats' and isinstance(result, dict):
                stats = result
            elif fname == 'sdr_profile' and isinstance(result, dict):
                sdr_profile = result
            elif fname == 'today_queue' and isinstance(result, dict):
                today_queue = result
            elif fname == 'recent_activities' and isinstance(result, list):
                recent_activities = result
            elif fname == 'source_performance' and isinstance(result, list):
                source_performance = result
            elif fname == 'pipeline_snapshot' and isinstance(result, dict):
                pipeline_snapshot = result
            elif fname == 'ai_recommendations' and isinstance(result, list):
                ai_recommendations = result
        except Exception as e:
            logger.warning(f'Failed to build {fname}: {e}')

    # Get Maton meetings with timeout
    import threading
    maton_result = []
    def _fetch_maton():
        try:
            maton_result.append(_build_maton_meetings(workspace_id))
        except Exception:
            pass
    t = threading.Thread(target=_fetch_maton)
    t.start()
    t.join(timeout=8)
    maton_meetings = maton_result[0] if maton_result else []

    return jsonify({
        'stats': stats,
        'sdr_profile': sdr_profile,
        'today_queue': today_queue,
        'recent_activities': recent_activities,
        'source_performance': source_performance,
        'pipeline_snapshot': pipeline_snapshot,
        'ai_recommendations': ai_recommendations,
        'maton_meetings': maton_meetings,
    })

