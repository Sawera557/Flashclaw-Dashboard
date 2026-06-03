"""HubSpot CRM routes — pipeline dashboard + owner drill-down."""

import logging, os
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.services.hubspot_service import (
    get_owners, get_open_deals_by_owner, get_deals_for_owner,
    search_deals_by_owner_name, search_owner_by_name,
    build_context_summary, HubSpotError,
)

hubspot_bp = Blueprint('hubspot', __name__)
logger = logging.getLogger(__name__)


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
        me = next((o for o in owners if o['id'] == '101551557'), {'id': '101551557', 'name': 'Anna Jordan', 'email': ''})
        
        # My last 30 days deals
        deals = get_deals_for_owner('101551557', limit=100, last_days=30)
        
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
