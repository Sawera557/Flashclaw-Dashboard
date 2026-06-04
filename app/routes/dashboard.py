"""Dashboard API — aggregated SDR command center data.

GET /api/dashboard/summary — returns KPI stats, SDR profile, today queue,
activity timeline, source performance, pipeline snapshot, AI recommendations.
All data is workspace-aware and user-aware via JWT.
"""

import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from app import db
from app.models.user import User
from app.models.lead import Lead, LinkedInActivity, GeneratedEmail
from app.models.activity import LeadActivity, EmailActivity, Meeting, HubspotDeal

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint('dashboard', __name__)


def _get_user(user_id_str):
    try:
        return User.query.get(int(user_id_str))
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
    return dt.isoformat() if dt else None


# ── Stats builder ────────────────────────────────────────────────────


def _build_stats(workspace_id, user_id):
    """KPI calculations — total, new, enriched, emails, replies, meetings, pipeline value, conversion."""
    today_start = _today_start()
    today_end = _today_end()

    # Lead counts
    total_leads = Lead.query.filter_by(workspace_id=workspace_id).count()
    new_today = Lead.query.filter(
        Lead.workspace_id == workspace_id,
        Lead.created_at >= today_start,
        Lead.created_at <= today_end,
    ).count()

    enriched = Lead.query.filter(
        Lead.workspace_id == workspace_id,
        Lead.enriched_at.isnot(None),
    ).count()

    # Email stats (from EmailActivity table)
    emails_sent = EmailActivity.query.filter(
        EmailActivity.workspace_id == workspace_id,
        EmailActivity.status == 'sent',
    ).count()

    replies_received = EmailActivity.query.filter(
        EmailActivity.workspace_id == workspace_id,
        EmailActivity.status == 'replied',
    ).count()

    positive_replies = EmailActivity.query.filter(
        EmailActivity.workspace_id == workspace_id,
        EmailActivity.reply_sentiment == 'positive',
    ).count()

    # Fallback to LinkedInActivity if no email_activities exist yet
    # (covers legacy data path)
    if replies_received == 0:
        legacy_replies = LinkedInActivity.query.filter(
            LinkedInActivity.workspace_id == workspace_id,
            LinkedInActivity.activity_type.in_(['reply_received', 'interested']),
        ).count()
        replies_received = max(replies_received, legacy_replies)
        legacy_positive = LinkedInActivity.query.filter(
            LinkedInActivity.workspace_id == workspace_id,
            LinkedInActivity.activity_type == 'interested',
        ).count()
        if positive_replies == 0:
            positive_replies = legacy_positive

    # Meetings
    meetings_booked = Meeting.query.filter(
        Meeting.workspace_id == workspace_id,
        Meeting.status == 'scheduled',
    ).count()
    meetings_completed = Meeting.query.filter(
        Meeting.workspace_id == workspace_id,
        Meeting.status == 'completed',
    ).count()
    total_meetings = meetings_booked + meetings_completed

    # Pipeline value from HubspotDeal cache
    pipeline_deals = HubspotDeal.query.filter(
        HubspotDeal.workspace_id == workspace_id,
    ).all()
    pipeline_value = sum(d.amount or 0 for d in pipeline_deals)

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
    workspace_id = user.workspace_id

    # Streak: consecutive days with activity
    today_start = _today_start()
    streak = 0
    check_date = datetime.now(timezone.utc).date()
    for days_back in range(0, 60):
        day_start = datetime(check_date.year, check_date.month, check_date.day, tzinfo=timezone.utc) - timedelta(days=days_back)
        day_end = day_start + timedelta(hours=23, minutes=59, seconds=59)

        had_activity = (
            LinkedInActivity.query.filter(
                LinkedInActivity.workspace_id == workspace_id,
                LinkedInActivity.user_id == user.id,
                LinkedInActivity.created_at >= day_start,
                LinkedInActivity.created_at <= day_end,
            ).first()
            or EmailActivity.query.filter(
                EmailActivity.workspace_id == workspace_id,
                EmailActivity.user_id == user.id,
                EmailActivity.created_at >= day_start,
                EmailActivity.created_at <= day_end,
            ).first()
        )

        if had_activity:
            streak += 1
        elif days_back > 0:
            # Allow gap for today if it's still early
            if days_back == 0:
                continue
            break

    # Rank based on total leads generated in workspace
    user_leads = Lead.query.filter_by(
        workspace_id=workspace_id, user_id=user.id
    ).count()

    total_workspace_leads = Lead.query.filter_by(workspace_id=workspace_id).count()

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
    team_members = User.query.filter(
        User.workspace_id == workspace_id,
        User.role == 'sdr',
    ).count()

    return {
        'name': user.name,
        'email': user.email,
        'role': user.role.capitalize(),
        'avatar': user.avatar or user.name[:2].upper(),
        'team_size': team_members + 1,  # +1 for self
        'streak': streak,
        'rank': rank,
        'leads_generated': user_leads,
    }


# ── Today's Work Queue ──────────────────────────────────────────────


def _build_today_queue(workspace_id, user_id):
    """Follow-ups due, positive replies needing response, meetings today, leads needing enrichment."""
    today_start = _today_start()
    today_end = _today_end()

    # Follow-ups due: leads with status 'contacted' or 'replied'
    follow_ups_due = Lead.query.filter(
        Lead.workspace_id == workspace_id,
        Lead.user_id == user_id,
        Lead.status.in_(['contacted', 'replied']),
    ).count()

    # Positive replies needing response: EmailActivity with replied + positive sentiment, no follow-up yet
    positive_reply_ids = set()
    positive_emails = EmailActivity.query.filter(
        EmailActivity.workspace_id == workspace_id,
        EmailActivity.user_id == user_id,
        EmailActivity.reply_sentiment == 'positive',
        EmailActivity.replied_at.isnot(None),
    ).all()

    # Check if we've followed up — count how many have a reply_email_activity after
    needs_response = 0
    for email in positive_emails:
        # Look for a sent email after this reply (meaning we responded)
        responded = EmailActivity.query.filter(
            EmailActivity.workspace_id == workspace_id,
            EmailActivity.user_id == user_id,
            EmailActivity.lead_id == email.lead_id,
            EmailActivity.status == 'sent',
            EmailActivity.sent_at > email.replied_at,
        ).first()
        if not responded:
            needs_response += 1

    # Also check LinkedInActivity for interested
    interested_activities = LinkedInActivity.query.filter(
        LinkedInActivity.workspace_id == workspace_id,
        LinkedInActivity.user_id == user_id,
        LinkedInActivity.activity_type == 'interested',
    ).all()

    for act in interested_activities:
        needs_response += 1

    # Meetings today
    meetings_today = Meeting.query.filter(
        Meeting.workspace_id == workspace_id,
        Meeting.user_id == user_id,
        Meeting.status == 'scheduled',
        Meeting.scheduled_at >= today_start,
        Meeting.scheduled_at <= today_end,
    ).count()

    # Leads needing enrichment (no enriched_at, score < 50)
    needs_enrichment = Lead.query.filter(
        Lead.workspace_id == workspace_id,
        Lead.enriched_at.is_(None),
        Lead.lead_score < 50,
    ).count()

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
    lead_acts = LeadActivity.query.filter(
        LeadActivity.workspace_id == workspace_id,
        LeadActivity.user_id == user_id,
    ).order_by(LeadActivity.created_at.desc()).limit(limit).all()

    for a in lead_acts:
        entries.append({
            'type': a.activity_type,
            'description': a.description,
            'timestamp': _fmt_date(a.created_at),
            'icon': '📋',
        })

    # Email activities
    email_acts = EmailActivity.query.filter(
        EmailActivity.workspace_id == workspace_id,
        EmailActivity.user_id == user_id,
    ).order_by(EmailActivity.created_at.desc()).limit(limit).all()

    for e in email_acts:
        icon_map = {'sent': '📤', 'opened': '👁️', 'clicked': '🖱️', 'replied': '💬', 'bounced': '❌'}
        entries.append({
            'type': f'email_{e.status}',
            'description': f'{e.status.capitalize()}: {e.subject[:80]} → {e.recipient}',
            'timestamp': _fmt_date(e.sent_at or e.created_at),
            'icon': icon_map.get(e.status, '📧'),
        })

    # LinkedIn activities
    li_acts = LinkedInActivity.query.filter(
        LinkedInActivity.workspace_id == workspace_id,
        LinkedInActivity.user_id == user_id,
    ).order_by(LinkedInActivity.created_at.desc()).limit(limit).all()

    icon_li_map = {
        'connection_sent': '🔗', 'connection_accepted': '✅',
        'dm_sent': '💌', 'reply_received': '📬',
        'interested': '👍', 'not_interested': '👎',
        'meeting_booked': '📅', 'followup_sent': '🔄',
        'profile_viewed': '👀',
    }
    for a in li_acts:
        desc = a.notes or f'{a.activity_type.replace("_", " ").title()} — {a.lead_name}'
        entries.append({
            'type': f'linkedin_{a.activity_type}',
            'description': desc[:120],
            'timestamp': _fmt_date(a.created_at),
            'icon': icon_li_map.get(a.activity_type, '🔗'),
        })

    # Meetings
    meetings = Meeting.query.filter(
        Meeting.workspace_id == workspace_id,
        Meeting.user_id == user_id,
    ).order_by(Meeting.created_at.desc()).limit(limit).all()

    for m in meetings:
        status_icon = {'scheduled': '📅', 'completed': '✅', 'cancelled': '❌', 'no_show': '🚫'}
        desc = f'Meeting {m.status}: {m.title or m.meeting_type}'
        entries.append({
            'type': f'meeting_{m.status}',
            'description': desc,
            'timestamp': _fmt_date(m.scheduled_at or m.created_at),
            'icon': status_icon.get(m.status, '📅'),
        })

    # Sort by timestamp desc, take top limit
    entries.sort(key=lambda x: x.get('timestamp') or '', reverse=True)
    return entries[:limit]


# ── Source Performance ──────────────────────────────────────────────


def _build_source_performance(workspace_id):
    """Lead source performance: volume, reply rate, quality score per source."""
    sources = db.session.query(
        Lead.source,
        db.func.count(Lead.id).label('lead_count'),
        db.func.avg(Lead.lead_score).label('avg_score'),
    ).filter(
        Lead.workspace_id == workspace_id,
        Lead.source.isnot(None),
        Lead.source != '',
    ).group_by(Lead.source).all()

    # Enrichment email stats per source (join through leads)
    results = []
    for source_row in sources:
        source_name = source_row.source
        lead_count = source_row.lead_count
        avg_score = round(float(source_row.avg_score or 0), 1)

        # Count replies for leads from this source
        source_leads = Lead.query.filter(
            Lead.workspace_id == workspace_id,
            Lead.source == source_name,
        ).all()
        source_lead_ids = [l.id for l in source_leads]

        # Email replies for these leads
        email_replies = 0
        if source_lead_ids:
            email_replies = EmailActivity.query.filter(
                EmailActivity.lead_id.in_(source_lead_ids),
                EmailActivity.status == 'replied',
            ).count()

            # Also check LinkedIn activity
            li_replies = 0
            for l in source_leads:
                li_replies += LinkedInActivity.query.filter(
                    LinkedInActivity.workspace_id == workspace_id,
                    LinkedInActivity.lead_name.ilike(f'%{l.first_name}%'),
                    LinkedInActivity.company.ilike(f'%{l.company}%'),
                    LinkedInActivity.activity_type.in_(['reply_received', 'interested']),
                ).count()
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
    deals = HubspotDeal.query.filter_by(workspace_id=workspace_id).all()

    if deals:
        stage_map = {}
        for d in deals:
            s = d.stage or 'Unknown'
            if s not in stage_map:
                stage_map[s] = {'count': 0, 'value': 0.0}
            stage_map[s]['count'] += 1
            stage_map[s]['value'] += (d.amount or 0)

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
            'total_value': sum(d.amount or 0 for d in deals),
            'stages': stages,
        }

    # Fallback: build from lead statuses
    status_leads = db.session.query(
        Lead.status,
        db.func.count(Lead.id),
    ).filter(
        Lead.workspace_id == workspace_id,
    ).group_by(Lead.status).all()

    stages = [
        {
            'stage': s.capitalize(),
            'count': count,
            'value': 0,
        }
        for s, count in status_leads
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
    today_start = _today_start()
    today_li = LinkedInActivity.query.filter(
        LinkedInActivity.workspace_id == workspace_id,
        LinkedInActivity.user_id == user_id,
        LinkedInActivity.created_at >= today_start,
    ).count()
    today_email = EmailActivity.query.filter(
        EmailActivity.workspace_id == workspace_id,
        EmailActivity.user_id == user_id,
        EmailActivity.created_at >= today_start,
    ).count()
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
    workspace_id = user.workspace_id

    if Meeting.query.filter_by(workspace_id=workspace_id).first():
        return  # Already seeded

    # Demo meetings
    now = datetime.now(timezone.utc)

    leads = Lead.query.filter_by(workspace_id=workspace_id).all()
    if not leads:
        return

    demo_meetings = [
        {
            'lead_id': leads[4].id if len(leads) > 4 else leads[0].id,
            'title': 'Demo: CloudSecure AI platform',
            'meeting_type': 'demo',
            'scheduled_at': now + timedelta(hours=3),
            'duration_minutes': 30,
            'status': 'scheduled',
        },
        {
            'lead_id': leads[3].id if len(leads) > 3 else leads[0].id,
            'title': 'Discovery call — Ecommerce Scale',
            'meeting_type': 'discovery',
            'scheduled_at': now + timedelta(days=1, hours=10),
            'duration_minutes': 45,
            'status': 'scheduled',
        },
        {
            'lead_id': leads[2].id if len(leads) > 2 else leads[0].id,
            'title': 'Follow-up: HealthTech AI',
            'meeting_type': 'followup',
            'scheduled_at': now - timedelta(days=2),
            'duration_minutes': 30,
            'status': 'completed',
            'notes': 'Interested in pilot program. Follow up next week.',
        },
    ]

    for m in demo_meetings:
        meeting = Meeting(
            workspace_id=workspace_id,
            user_id=user.id,
            **m,
        )
        db.session.add(meeting)

    # Demo email activities
    demo_emails = [
        {
            'lead_id': leads[0].id,
            'subject': 'Quick question about TechStartup.io',
            'recipient': 'sarah.chen@techstartup.io',
            'status': 'sent',
            'sent_at': now - timedelta(days=5),
            'email_type': 'cold',
        },
        {
            'lead_id': leads[0].id,
            'subject': 'Re: Quick question about TechStartup.io',
            'recipient': 'sarah.chen@techstartup.io',
            'status': 'replied',
            'sent_at': now - timedelta(days=4),
            'replied_at': now - timedelta(days=4, hours=6),
            'reply_sentiment': 'neutral',
            'email_type': 'followup',
        },
        {
            'lead_id': leads[3].id if len(leads) > 3 else leads[0].id,
            'subject': 'Growth opportunities for Ecommerce Scale',
            'recipient': 'jwilson@ecommercescale.com',
            'status': 'replied',
            'sent_at': now - timedelta(days=2),
            'replied_at': now - timedelta(days=1),
            'reply_sentiment': 'positive',
            'email_type': 'cold',
        },
        {
            'lead_id': leads[4].id if len(leads) > 4 else leads[0].id,
            'subject': 'Security solutions for CloudSecure',
            'recipient': 'aisha@cloudsecure.dev',
            'status': 'sent',
            'sent_at': now - timedelta(hours=12),
            'email_type': 'cold',
        },
        {
            'lead_id': leads[1].id if len(leads) > 1 else leads[0].id,
            'subject': 'Fintech Pro — partnership opportunity',
            'recipient': 'marcus@fintechpro.com',
            'status': 'sent',
            'sent_at': now - timedelta(hours=6),
            'email_type': 'cold',
        },
    ]

    for e in demo_emails:
        email = EmailActivity(
            workspace_id=workspace_id,
            user_id=user.id,
            **e,
        )
        db.session.add(email)

    # Demo lead activities
    demo_lead_activities = [
        {
            'lead_id': leads[0].id,
            'activity_type': 'enrichment',
            'description': 'Enriched Sarah Chen — added company size and industry',
        },
        {
            'lead_id': leads[0].id,
            'activity_type': 'score',
            'description': 'Scored Sarah Chen at 85 — strong ICP match',
        },
        {
            'lead_id': leads[4].id if len(leads) > 4 else leads[0].id,
            'activity_type': 'status_change',
            'description': f'Changed Aisha Patel status to meeting_booked',
        },
    ]

    for a in demo_lead_activities:
        activity = LeadActivity(
            workspace_id=workspace_id,
            user_id=user.id,
            created_at=now - timedelta(hours=random_offset()),
            **a,
        )
        db.session.add(activity)

    db.session.commit()
    logger.info('Seeded demo dashboard data')


import random
def random_offset():
    return random.randint(1, 72)


# ── Main Endpoint ────────────────────────────────────────────────────


@dashboard_bp.route('/api/dashboard/summary', methods=['GET'])
@jwt_required()
def dashboard_summary():
    """Main dashboard summary endpoint — all data in one response."""
    current_user_id = get_jwt_identity()
    user = _get_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    workspace_id = user.workspace_id

    # Seed demo data on first run if empty
    _seed_dashboard_data(user)

    try:
        stats = _build_stats(workspace_id, user.id)
        sdr_profile = _build_sdr_profile(user)
        today_queue = _build_today_queue(workspace_id, user.id)
        recent_activities = _build_recent_activities(workspace_id, user.id)
        source_performance = _build_source_performance(workspace_id)
        pipeline_snapshot = _build_pipeline_snapshot(workspace_id)
        ai_recommendations = _build_ai_recommendations(
            workspace_id, user.id, stats, today_queue
        )

        return jsonify({
            'stats': stats,
            'sdr_profile': sdr_profile,
            'today_queue': today_queue,
            'recent_activities': recent_activities,
            'source_performance': source_performance,
            'pipeline_snapshot': pipeline_snapshot,
            'ai_recommendations': ai_recommendations,
        })

    except Exception as e:
        logger.exception('dashboard_summary error')
        return jsonify({'error': f'Failed to build dashboard: {str(e)}'}), 500
