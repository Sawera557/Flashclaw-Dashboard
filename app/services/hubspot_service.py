"""HubSpot service — fetches owners, deals, contacts from HubSpot API."""

import json, os, time, logging, urllib.request, urllib.error
from collections import defaultdict

logger = logging.getLogger(__name__)

class HubSpotError(Exception): pass

def _get_token():
    token = os.environ.get('HUBSPOT_ACCESS_TOKEN', '').strip()
    if not token: raise HubSpotError('HUBSPOT_ACCESS_TOKEN not configured')
    return token

def _api_get(path, params=None):
    token = _get_token()
    url = f'https://api.hubapi.com{path}'
    if params:
        import urllib.parse
        url += '?' + urllib.parse.urlencode(params)
    for _ in range(3):
        try:
            req = urllib.request.Request(url)
            req.add_header('Authorization', f'Bearer {token}')
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(int(e.headers.get('Retry-After', '5'))); continue
            raise HubSpotError(f'API error {e.code}: {e.read().decode()[:300]}')
        except Exception:
            time.sleep(1); continue
    raise HubSpotError('Max retries exceeded')

def _api_post(path, body):
    token = _get_token()
    url = f'https://api.hubapi.com{path}'
    for _ in range(3):
        try:
            data = json.dumps(body).encode()
            req = urllib.request.Request(url, data=data, method='POST')
            req.add_header('Authorization', f'Bearer {token}')
            req.add_header('Content-Type', 'application/json')
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(int(e.headers.get('Retry-After', '5'))); continue
            raise HubSpotError(f'API error {e.code}: {e.read().decode()[:300]}')
        except Exception:
            time.sleep(1); continue
    raise HubSpotError('Max retries exceeded')

STAGE_LABELS = {
    '1267337117': 'Opp. Identified', 'appointmentscheduled': '1st Meeting Scheduled',
    '45777983': '1st Meeting Held, 2nd Scheduled', '69041298': '2nd Meeting Held, Eval Scheduled',
    'qualifiedtobuy': 'Qualified to Buy', 'presentationscheduled': 'Presentation Scheduled',
    'decisionmakersboughtin': 'Dec. Makers Bought In', 'contractsent': 'Contract Sent',
    '126902562': 'Needs Assessment', '12894807': 'Pricing/Negotiation',
    'closedwon': 'Closed Won', 'closedlost': 'Closed Lost',
}
CLOSED_STAGES = {'closedwon', 'closedlost'}
OPEN_STAGES = [k for k in STAGE_LABELS if k not in CLOSED_STAGES]

def get_owners():
    data = _api_get('/crm/v3/owners', {'limit': 200})
    return [{'id': o['id'], 'name': f"{o.get('firstName','')} {o.get('lastName','')}".strip() or o.get('email',''), 'email': o.get('email','')} for o in data.get('results',[])]

def fetch_open_deals(max_pages=20):
    all_deals = []; after = None
    for _ in range(max_pages):
        body = {'filterGroups': [{'filters': [{'propertyName': 'dealstage', 'operator': 'IN', 'values': OPEN_STAGES}]}], 'sorts': [{'propertyName': 'createdate', 'direction': 'DESCENDING'}], 'limit': 100, 'properties': ['dealname','amount','dealstage','createdate','hs_lastmodifieddate','hubspot_owner_id']}
        if after: body['after'] = after
        data = _api_post('/crm/v3/objects/deals/search', body)
        for d in data.get('results', []):
            p = d['properties']
            all_deals.append({'name': p.get('dealname','?'), 'amount': p.get('amount') or '0', 'stage_id': p.get('dealstage',''), 'stage': STAGE_LABELS.get(p.get('dealstage',''), p.get('dealstage','')), 'created': (p.get('createdate') or '')[:10], 'modified': (p.get('hs_lastmodifieddate') or '')[:10], 'owner_id': p.get('hubspot_owner_id','')})
        if not data.get('paging'): break
        after = data['paging'].get('next', {}).get('after')
        if not after: break
        time.sleep(0.25)
    return all_deals

def get_open_deals_by_owner():
    owners_map = {o['id']: o['name'] for o in get_owners()}
    deals = fetch_open_deals(max_pages=20)
    by_owner = defaultdict(list)
    for d in deals: by_owner[d.get('owner_id','unassigned')].append(d)
    entries = []
    for oid, deal_list in sorted(by_owner.items(), key=lambda x: len(x[1]), reverse=True):
        deal_list.sort(key=lambda d: d['modified'], reverse=True)
        total_val = sum(int(float(d.get('amount',0) or 0)) for d in deal_list)
        name = owners_map.get(oid, f'Owner {oid}')
        entries.append({'id': oid, 'name': name, 'email': '', 'deal_count': len(deal_list), 'total_value': total_val, 'latest_deals': deal_list[:10]})
    owners_full = get_owners()
    email_map = {o['id']: o['email'] for o in owners_full}
    for e in entries: e['email'] = email_map.get(e['id'], '')
    return {'owners': entries, 'total_open': len(deals), 'total_owners': len(entries)}

def get_deals_for_owner(owner_id, limit=20, include_closed=False):
    filters = [{'propertyName': 'hubspot_owner_id', 'operator': 'EQ', 'value': str(owner_id)}]
    if not include_closed: filters.append({'propertyName': 'dealstage', 'operator': 'IN', 'values': OPEN_STAGES})
    data = _api_post('/crm/v3/objects/deals/search', {'filterGroups': [{'filters': filters}], 'sorts': [{'propertyName': 'hs_lastmodifieddate', 'direction': 'DESCENDING'}], 'limit': limit, 'properties': ['dealname','amount','dealstage','createdate','hs_lastmodifieddate','hubspot_owner_id']})
    return [{'id': d['id'], 'name': p.get('dealname','?'), 'amount': p.get('amount') or '0', 'stage': STAGE_LABELS.get(p.get('dealstage',''), p.get('dealstage','')), 'stage_id': p.get('dealstage',''), 'created': (p.get('createdate') or '')[:10], 'modified': (p.get('hs_lastmodifieddate') or '')[:10], 'owner_id': p.get('hubspot_owner_id','')} for d in data.get('results',[]) if (p := d['properties'])]

def search_owner_by_name(query):
    q = query.lower().strip()
    return [o for o in get_owners() if q in o['name'].lower() or q in o['email'].lower()]

def search_deals_by_owner_name(query, limit=10):
    matched = search_owner_by_name(query); results = []
    for owner in matched[:5]:
        deals = get_deals_for_owner(owner['id'], limit=limit)
        if deals: results.append({'owner': owner, 'deals': deals})
    return results

def build_context_summary():
    data = get_open_deals_by_owner()
    lines = [f'HUBSPOT CRM: {data["total_open"]} open deals, {data["total_owners"]} owners']
    for o in data['owners'][:15]:
        val = f"${o['total_value']:,}" if o['total_value'] else '$0'
        lines.append(f'  {o["name"]} — {o["deal_count"]} deals ({val})')
        for d in o['latest_deals'][:3]:
            lines.append(f'    · {d["name"]} — ${d["amount"]} — {d["stage"]}')
    return '\n'.join(lines)
