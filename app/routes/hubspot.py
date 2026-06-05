"""HubSpot CRM routes — pipeline dashboard + owner drill-down."""

import logging, os, requests
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.services.hubspot_service import (
    get_owners, get_open_deals_by_owner, get_deals_for_owner,
    search_deals_by_owner_name, search_owner_by_name,
    build_context_summary, HubSpotError,
)

hubspot_bp = Blueprint('hubspot', __name__)
logger = logging.getLogger(__name__)

SLACK_WEBHOOK_URL = os.getenv('SLACK_WEBHOOK_URL', '')
HUBSPOT_PORTAL_ID = os.getenv('HUBSPOT_PORTAL_ID', '22606445')


def _detect_activity_type(event):
    st = str(event.get('subscriptionType', '')).lower()
    pn = str(event.get('propertyName', '')).lower()
    cs = str(event.get('changeSource', '')).lower()
    text = f"{st} {pn} {cs}"
    if 'deal' in text and 'dealstage' in text: return 'Deal stage changed'
    if 'deal' in text and 'created' in text: return 'New deal created'
    if 'deal' in text: return 'Deal updated'
    if 'contact' in text and 'created' in text: return 'New contact created'
    if 'contact' in text: return 'Contact updated'
    if 'company' in text and 'created' in text: return 'New company created'
    if 'company' in text: return 'Company updated'
    if 'lifecyclestage' in text: return 'Lifecycle stage changed'
    if 'hubspot_owner_id' in text: return 'Owner changed'
    return 'HubSpot activity'


def _hubspot_url(event):
    oid = event.get('objectId')
    st = str(event.get('subscriptionType', '')).split('.')[0].lower()
    if not HUBSPOT_PORTAL_ID or not oid: return ''
    base = f'https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}'
    if st == 'contact': return f'{base}/contact/{oid}'
    if st == 'company': return f'{base}/company/{oid}'
    if st == 'deal': return f'{base}/deal/{oid}'
    return base


def _build_event_blocks(event):
    """Build Slack blocks for one event as a single readable sentence."""
    oid = event.get('objectId', 'unknown')
    pn = event.get('propertyName', '')
    pv = event.get('propertyValue', '')
    po = event.get('previousValue', '')
    cs = event.get('changeSource', '')
    sid = event.get('sourceId', '')
    st = event.get('subscriptionType', '')

    url = _hubspot_url(event)
    obj_link = f"<{url}|#{oid}>" if url else f"#{oid}"
    ts = event.get('occurredAt')

    # Timestamp badge
    time_badge = ''
    if ts:
        try:
            dt_utc = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
            dt_local = dt_utc.astimezone()
            time_badge = dt_local.strftime('%I:%M %p').lstrip('0')
        except Exception:
            pass

    ts_prefix = f"`[{time_badge}]` " if time_badge else ''

    # Source label
    source_map = {
        'CRM_UI': 'HubSpot UI',
        'API': 'API',
        'IMPORT': 'Import',
        'INTEGRATION': 'Integration',
        'AUTOMATION': 'Workflow',
    }
    source_label = source_map.get(cs, cs.replace('_', ' ').title() if cs else '')
    via = f" via {source_label}" if source_label else ""

    # Object type
    obj_type = st.split('.')[0] if '.' in st else 'record'
    obj_name = {'deal': 'Deal', 'contact': 'Contact', 'company': 'Company'}.get(obj_type, obj_type.capitalize())

    is_creation = 'creation' in st.lower()
    is_stage = 'dealstage' == pn
    is_owner = 'hubspot_owner_id' == pn

    # --- Build natural sentence ---
    action = ''
    detail = ''

    if is_creation:
        action = 'was created'
        if pn and pv:
            detail = f" \u2014 {pn} set to *{pv}*"
    elif is_stage and po and pv:
        action = f"stage moved from *{po}* \u2192 *{pv}*"
    elif is_owner and po and pv:
        action = f"owner changed from *{po}* \u2192 *{pv}*"
    elif po and pv:
        action = f"*{pn}* changed from *{po}* \u2192 *{pv}*"
    elif pv:
        action = f"*{pn}* set to *{pv}*"
    elif po:
        action = f"*{pn}* was cleared (was {po})"
    else:
        action = 'was updated'

    sentence = f"{ts_prefix}{obj_name} {obj_link} {action}{detail}{via}"

    # Link footer
    footer = f"<{url}|\U0001F517 Open in HubSpot>" if url else ''

    blocks = [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': sentence}}]
    if footer:
        blocks.append({'type': 'section', 'text': {'type': 'mrkdwn', 'text': footer}})      

    return blocks


def _send_batched_slack(events):
    """Batch events into a single Slack message with dividers between cards."""
    MAX_EVENTS = 10
    for chunk in [events[i:i+MAX_EVENTS] for i in range(0, len(events), MAX_EVENTS)]:
        blocks = []
        for idx, event in enumerate(chunk):
            if idx > 0:
                blocks.append({'type': 'divider'})
            blocks.extend(_build_event_blocks(event))

        payload = {
            'text': f"HubSpot: {len(chunk)} activity event(s)",
            'blocks': blocks
        }
        try:
            requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
        except Exception as e:
            logger.warning(f'Slack webhook batch failed: {e}')
@hubspot_bp.route('/api/hubspot/status', methods=['GET'])
@jwt_required()
def status():
    token = os.environ.get('HUBSPOT_ACCESS_TOKEN', '')
    return jsonify({'configured': bool(token), 'preview': (token[:12] + '...') if token else None})


@hubspot_bp.route('/api/hubspot/dashboard', methods=['GET'])
@jwt_required()
def dashboard():
    try: return jsonify(get_open_deals_by_owner())
    except HubSpotError as e: return jsonify({'error': str(e)}), 502


@hubspot_bp.route('/api/hubspot/owner/<owner_id>/deals', methods=['GET'])
@jwt_required()
def owner_deals(owner_id):
    limit = request.args.get('limit', 20, type=int)
    closed = request.args.get('closed', 'false').lower() == 'true'
    last_days = request.args.get('last_days', type=int)
    try:
        deals = get_deals_for_owner(owner_id, limit=limit, include_closed=closed, last_days=last_days)
        owners = get_owners()
        info = next((o for o in owners if o['id'] == owner_id), {'id': owner_id, 'name': f'Owner {owner_id}'})
        return jsonify({'owner': info, 'deals': deals, 'count': len(deals)})
    except HubSpotError as e: return jsonify({'error': str(e)}), 502


@hubspot_bp.route('/api/hubspot/search/owners', methods=['GET'])
@jwt_required()
def search_owners():
    q = request.args.get('q', '').strip()
    if not q: return jsonify({'error': 'q required'}), 400
    try:
        r = search_owner_by_name(q)
        return jsonify({'results': r, 'count': len(r)})
    except HubSpotError as e: return jsonify({'error': str(e)}), 502



@hubspot_bp.route('/api/hubspot/my-dashboard', methods=['GET'])
@jwt_required()
def my_dashboard():
    """Returns Anna Jordan's deals (last 30 days) with dashboard-friendly stats."""
    from app.services.hubspot_service import get_deals_for_owner, get_owners
    import math
    try:
        # Cache owner info
        owners = get_owners()
        me = next((o for o in owners if o['id'] == '555925314'), {'id': '555925314', 'name': 'Kathy Roggers', 'email': ''})
        
        # My last 6 months (180 days) deals
        deals = get_deals_for_owner('555925314', limit=100, last_days=180)
        
        total_val = sum(int(float(d.get('amount', 0) or 0)) for d in deals)
        
        # Stage breakdown for pipeline chart
        stage_counts = {}
        for d in deals:
            s = d.get('stage', 'Unknown')
            stage_counts[s] = stage_counts.get(s, 0) + 1
        
        stages = [{'stage': s, 'count': c, 'pct': round(c/len(deals)*100) if deals else 0} for s, c in sorted(stage_counts.items(), key=lambda x: x[1], reverse=True)]
        
        # Timeline
        timeline = [{'desc': d['name'], 'stage': d['stage'], 'amount': d.get('amount', '0'), 'date': d.get('modified',''), 'time': d.get('modified','')[:10]} for d in deals[:20]]
        
        return jsonify({
            'owner': me,
            'total_deals': len(deals),
            'total_value': total_val,
            'avg_deal': math.floor(total_val / len(deals)) if deals else 0,
            'stages': stages,
            'deals': deals[:50],
            'timeline': timeline,
        })
    except Exception as e:
        logger.exception('my_dashboard')
        return jsonify({'error': str(e)}), 502


@hubspot_bp.route('/api/hubspot/search/deals', methods=['GET'])
@jwt_required()
def deals_by_owner():
    q = request.args.get('q', '').strip()
    if not q: return jsonify({'error': 'q required'}), 400
    try:
        r = search_deals_by_owner_name(q, limit=request.args.get('limit', 10, type=int))
        return jsonify({'results': r, 'count': len(r)})
    except HubSpotError as e: return jsonify({'error': str(e)}), 502


@hubspot_bp.route('/api/hubspot/webhook/activity', methods=['POST', 'GET'])
def hubspot_webhook_activity():
    """Receives HubSpot activity webhooks and forwards to Slack."""
    if request.method == 'GET':
        return jsonify({'ok': True, 'message': 'HubSpot webhook endpoint is live'})
    
    try:
        payload = request.get_json(force=True, silent=True)
        if not payload:
            payload = request.get_data(as_text=True) or '[]'
            import json as _json
            payload = _json.loads(payload)
        
        events = payload if isinstance(payload, list) else [payload]
        sent = 0
        
        if SLACK_WEBHOOK_URL and events:
            sent = len(events)
            _send_batched_slack(events)
        
        return jsonify({'ok': True, 'received': len(events), 'batched_to_slack': sent})
    except Exception as e:
        logger.exception('hubspot_webhook_activity')
        return jsonify({'ok': False, 'error': str(e)}), 500
