"""HubSpot service — fetches owners, deals, contacts from HubSpot API.

Uses a private app access token stored in the HUBSPOT_ACCESS_TOKEN env var.
This is the single source of truth for HubSpot data in the Dashboard.
"""

import json
import os
import time
import logging
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)


class HubSpotError(Exception):
    pass


def _get_token():
    token = os.environ.get('HUBSPOT_ACCESS_TOKEN', '').strip()
    if not token:
        raise HubSpotError('HUBSPOT_ACCESS_TOKEN not configured')
    return token


def _api_get(path, params=None, max_retries=2):
    """Make a GET request to HubSpot API."""
    token = _get_token()
    url = f'https://api.hubapi.com{path}'
    if params:
        import urllib.parse
        url += '?' + urllib.parse.urlencode(params)

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url)
            req.add_header('Authorization', f'Bearer {token}')
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get('Retry-After', '5'))
                time.sleep(wait)
                continue
            body = e.read().decode()[:500]
            raise HubSpotError(f'HubSpot API error {e.code}: {body}')
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
            raise HubSpotError(str(e))


def _api_post(path, body, max_retries=2):
    """Make a POST request to HubSpot API."""
    token = _get_token()
    url = f'https://api.hubapi.com{path}'

    for attempt in range(max_retries):
        try:
            data = json.dumps(body).encode()
            req = urllib.request.Request(url, data=data, method='POST')
            req.add_header('Authorization', f'Bearer {token}')
            req.add_header('Content-Type', 'application/json')
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get('Retry-After', '5'))
                time.sleep(wait)
                continue
            body_text = e.read().decode()[:500]
            raise HubSpotError(f'HubSpot API error {e.code}: {body_text}')
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
            raise HubSpotError(str(e))


# ─── Stage Labels ──────────────────────────────────────────────────────

STAGE_LABELS = {
    '1267337117': 'Opp. Identified',
    'appointmentscheduled': '1st Meeting Scheduled',
    '45777983': '1st Meeting Held, 2nd Scheduled',
    '69041298': '2nd Meeting Held, Eval Scheduled',
    'qualifiedtobuy': 'Qualified to Buy',
    'presentationscheduled': 'Presentation Scheduled',
    'decisionmakersboughtin': 'Dec. Makers Bought In',
    'contractsent': 'Contract Sent',
    '126902562': 'Needs Assessment',
    '12894807': 'Pricing/Negotiation',
    'closedwon': 'Closed Won',
    'closedlost': 'Closed Lost',
}

CLOSED_STAGES = {'closedwon', 'closedlost'}


def get_owners():
    """Fetch all owners from HubSpot.

    Returns list of {id, name, email}
    """
    data = _api_get('/crm/v3/owners', {'limit': 200})
    results = data.get('results', [])
    owners = []
    for o in results:
        owners.append({
            'id': o['id'],
            'name': f"{o.get('firstName', '')} {o.get('lastName', '')}".strip() or o.get('email', ''),
            'email': o.get('email', ''),
        })
    return owners


def get_open_deals_by_owner():
    """Aggregate all open deals grouped by owner.

    Returns:
        owners: list of owner summaries with deal_count, total_value, latest 10 deals
        total_open: total open deal count
    """
    owners_raw = get_owners()
    owner_map = {o['id']: o for o in owners_raw}

    deals = _paginate_open_deals()

    from collections import defaultdict
    by_owner = defaultdict(list)
    for d in deals:
        oid = d.get('hubspot_owner_id', 'unassigned')
        by_owner[oid].append(d)

    owner_entries = []
    for oid, deal_list in sorted(by_owner.items(), key=lambda x: len(x[1]), reverse=True):
        info = owner_map.get(oid, {'id': oid, 'name': f'Owner {oid}', 'email': ''})
        deal_list.sort(key=lambda x: x.get('modified', ''), reverse=True)
        total_val = sum(int(float(d.get('amount', 0) or 0)) for d in deal_list)
        owner_entries.append({
            'id': oid,
            'name': info['name'],
            'email': info['email'],
            'deal_count': len(deal_list),
            'total_value': total_val,
            'latest_deals': deal_list[:10],
        })

    return {
        'owners': owner_entries,
        'total_open': len(deals),
        'total_owners': len(owner_entries),
    }


def get_deals_for_owner(owner_id, limit=10, include_closed=False):
    """Get latest deals for a specific owner."""
    open_stages = [k for k in STAGE_LABELS if k not in CLOSED_STAGES]

    filters = [{
        'propertyName': 'hubspot_owner_id',
        'operator': 'EQ',
        'value': owner_id,
    }]
    if not include_closed:
        filters.append({
            'propertyName': 'dealstage',
            'operator': 'IN',
            'values': open_stages,
        })

    data = _api_post('/crm/v3/objects/deals/search', {
        'filterGroups': [{'filters': filters}],
        'sorts': [{'propertyName': 'hs_lastmodifieddate', 'direction': 'DESCENDING'}],
        'limit': limit,
        'properties': ['dealname', 'amount', 'dealstage', 'createdate', 'hs_lastmodifieddate', 'hubspot_owner_id'],
    })
    results = data.get('results', [])
    deals = []
    for d in results:
        p = d['properties']
        deals.append({
            'id': d['id'],
            'name': p.get('dealname', '?'),
            'amount': p.get('amount') or '0',
            'stage': STAGE_LABELS.get(p.get('dealstage', ''), p.get('dealstage', '')),
            'stage_id': p.get('dealstage', ''),
            'created': p.get('createdate', '')[:10] if p.get('createdate') else '',
            'modified': p.get('hs_lastmodifieddate', '')[:10] if p.get('hs_lastmodifieddate') else '',
            'owner_id': p.get('hubspot_owner_id', ''),
        })
    return deals


def search_deals_by_owner_name(query, limit=10):
    """Find deals by fuzzy-matching owner name.

    Uses owner list, then for each matching owner returns their deals.
    """
    owners = get_owners()
    query_lower = query.lower().strip()

    matched_owners = [
        o for o in owners
        if query_lower in o['name'].lower() or query_lower in o['email'].lower()
    ]

    results = []
    for owner in matched_owners:
        deals = get_deals_for_owner(owner['id'], limit=limit)
        if deals:
            results.append({
                'owner': owner,
                'deals': deals,
            })

    return results


def search_owner_by_name(name_query):
    """Find owners whose name or email contains the query."""
    owners = get_owners()
    q = name_query.lower().strip()
    return [o for o in owners if q in o['name'].lower() or q in o['email'].lower()]


def build_dashboard_context():
    """Build a text summary of HubSpot state for AI context injection."""
    data = get_open_deals_by_owner()
    lines = []
    lines.append(f'HUBSPOT DASHBOARD — {data["total_open"]} open deals across {data["total_owners"]} owners')
    lines.append('')
    for o in data['owners']:
        val = f"${o['total_value']:,}" if o['total_value'] else '$0'
        lines.append(f'  {o["name"]} — {o["deal_count"]} open deals ({val})')
        for d in o['latest_deals'][:5]:
            lines.append(f'    · {d["name"]} — ${d["amount"]} — {d["stage"]}')
    return '\n'.join(lines)


# ─── Helpers ───────────────────────────────────────────────────────────

def _paginate_open_deals():
    """Fetch all open deals with pagination (max 500 results per query)."""
    open_stages = [k for k in STAGE_LABELS if k not in CLOSED_STAGES]

    all_deals = []
    after = None

    while True:
        body = {
            'filterGroups': [{
                'filters': [{
                    'propertyName': 'dealstage',
                    'operator': 'IN',
                    'values': open_stages,
                }]
            }],
            'sorts': [{'propertyName': 'createdate', 'direction': 'DESCENDING'}],
            'limit': 100,
            'properties': ['dealname', 'amount', 'dealstage', 'createdate', 'hs_lastmodifieddate', 'hubspot_owner_id'],
        }
        if after:
            body['after'] = after

        data = _api_post('/crm/v3/objects/deals/search', body)
        results = data.get('results', [])
        total = data.get('total', 0)

        for d in results:
            p = d['properties']
            all_deals.append({
                'name': p.get('dealname', '?'),
                'amount': p.get('amount') or '0',
                'stage': p.get('dealstage', ''),
                'created': p.get('createdate', '')[:10] if p.get('createdate') else '',
                'modified': p.get('hs_lastmodifieddate', '')[:10] if p.get('hs_lastmodifieddate') else '',
                'hubspot_owner_id': p.get('hubspot_owner_id', ''),
            })

        if len(all_deals) >= total or not data.get('paging'):
            break
        after = data['paging'].get('next', {}).get('after')
        if not after:
            break
        time.sleep(0.3)

    return all_deals
