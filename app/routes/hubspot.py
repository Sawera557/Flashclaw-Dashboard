"""HubSpot routes — dashboard endpoints for owners, deals, and search.

All endpoints require JWT auth (same as other Lead Hunter endpoints).
Returns HubSpot data grouped by owner for the frontend dashboard.
"""

import logging
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from app import db
from app.models.user import User
from app.services.hubspot_service import (
    get_owners,
    get_open_deals_by_owner,
    get_deals_for_owner,
    search_deals_by_owner_name,
    search_owner_by_name,
    build_dashboard_context,
    HubSpotError,
)

hubspot_bp = Blueprint('hubspot', __name__)
logger = logging.getLogger(__name__)


def _get_user():
    user_id = get_jwt_identity()
    try:
        return User.query.get(int(user_id))
    except (ValueError, TypeError):
        return None


@hubspot_bp.route('/api/hubspot/status', methods=['GET'])
@jwt_required()
def status():
    """Check if HubSpot is configured and working."""
    user = _get_user()
    if not user:
        return jsonify({'error': 'User not found'}), 404

    import os
    token = os.environ.get('HUBSPOT_ACCESS_TOKEN', '')
    return jsonify({
        'configured': bool(token),
        'token_preview': token[:8] + '...' if token else None,
    })


@hubspot_bp.route('/api/hubspot/owners', methods=['GET'])
@jwt_required()
def list_owners():
    """List all HubSpot owners."""
    user = _get_user()
    if not user:
        return jsonify({'error': 'User not found'}), 404

    try:
        owners = get_owners()
        return jsonify({'owners': owners})
    except HubSpotError as e:
        return jsonify({'error': str(e)}), 502


@hubspot_bp.route('/api/hubspot/dashboard', methods=['GET'])
@jwt_required()
def dashboard():
    """HubSpot pipeline dashboard — open deals grouped by owner.

    Query params:
        owner_id (optional): filter to a specific owner
        target_amount (optional): show only deals above this amount
    """
    user = _get_user()
    if not user:
        return jsonify({'error': 'User not found'}), 404

    owner_id = request.args.get('owner_id', '').strip()
    target_amount = request.args.get('target_amount', '').strip()

    try:
        if owner_id:
            # Single owner view
            deals = get_deals_for_owner(owner_id, limit=50)
            # Get owner name
            owners = get_owners()
            owner_info = next((o for o in owners if o['id'] == owner_id), {'id': owner_id, 'name': f'Owner {owner_id}'})
            return jsonify({
                'owner': owner_info,
                'deals': deals,
                'deal_count': len(deals),
                'total_value': sum(int(d['amount'] or 0) for d in deals),
            })
        else:
            # Full dashboard
            data = get_open_deals_by_owner()
            if target_amount:
                try:
                    min_amt = int(target_amount)
                    for owner in data['owners']:
                        owner['latest_deals'] = [d for d in owner['latest_deals'] if int(d.get('amount', 0) or 0) >= min_amt]
                        owner['deal_count'] = len(owner['latest_deals'])
                    data['owners'] = [o for o in data['owners'] if o['deal_count'] > 0]
                except ValueError:
                    pass
            return jsonify(data)

    except HubSpotError as e:
        return jsonify({'error': str(e)}), 502


@hubspot_bp.route('/api/hubspot/owner/<owner_id>/deals', methods=['GET'])
@jwt_required()
def owner_deals(owner_id):
    """Get the latest deals for a specific owner.

    Query params:
        limit (int, default 10)
        include_closed (bool, default false)
    """
    user = _get_user()
    if not user:
        return jsonify({'error': 'User not found'}), 404

    limit = request.args.get('limit', 10, type=int)
    include_closed = request.args.get('include_closed', 'false').lower() == 'true'

    try:
        deals = get_deals_for_owner(owner_id, limit=limit, include_closed=include_closed)
        # Look up owner name
        owners = get_owners()
        owner_info = next((o for o in owners if o['id'] == owner_id),
                          {'id': owner_id, 'name': f'Owner {owner_id}', 'email': ''})
        return jsonify({
            'owner': owner_info,
            'deals': deals,
            'deal_count': len(deals),
        })
    except HubSpotError as e:
        return jsonify({'error': str(e)}), 502


@hubspot_bp.route('/api/hubspot/search/owners', methods=['GET'])
@jwt_required()
def search_owners():
    """Search owners by name or email."""
    user = _get_user()
    if not user:
        return jsonify({'error': 'User not found'}), 404

    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'error': 'Query param "q" is required'}), 400

    try:
        results = search_owner_by_name(q)
        return jsonify({'results': results, 'count': len(results)})
    except HubSpotError as e:
        return jsonify({'error': str(e)}), 502


@hubspot_bp.route('/api/hubspot/search/deals-by-owner', methods=['GET'])
@jwt_required()
def deals_by_owner_name():
    """Search deals by fuzzy owner name.

    Example: /api/hubspot/search/deals-by-owner?q=Anna
    """
    user = _get_user()
    if not user:
        return jsonify({'error': 'User not found'}), 404

    q = request.args.get('q', '').strip()
    limit = request.args.get('limit', 10, type=int)
    if not q:
        return jsonify({'error': 'Query param "q" is required'}), 400

    try:
        results = search_deals_by_owner_name(q, limit=limit)
        return jsonify({'results': results, 'count': len(results)})
    except HubSpotError as e:
        return jsonify({'error': str(e)}), 502


@hubspot_bp.route('/api/hubspot/ai-context', methods=['GET'])
@jwt_required()
def ai_context():
    """Generate a text summary of HubSpot state for AI prompt injection.

    The frontend calls this and injects it into the AI chat system prompt
    so the agent can answer questions about HubSpot.
    """
    user = _get_user()
    if not user:
        return jsonify({'error': 'User not found'}), 404

    try:
        context = build_dashboard_context()
        return jsonify({'context': context})
    except HubSpotError as e:
        return jsonify({'error': str(e), 'context': ''}), 502
