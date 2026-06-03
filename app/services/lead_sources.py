"""Unified lead data service — Apollo.io, Hunter.io, Serper.dev, Firecrawl."""
import os
import json
import logging
import urllib.request, urllib.parse

logger = logging.getLogger(__name__)


def _api_post(url, headers, data_dict, timeout=15):
    """Helper: POST JSON, return parsed response."""
    body = json.dumps(data_dict).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())


def _api_get(url, headers, timeout=15):
    """Helper: GET, return parsed response."""
    req = urllib.request.Request(url, headers=headers)
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())


# ── Apollo.io ──────────────────────────────────────────────────────────

def apollo_search(industry=None, location=None, company_size=None, title=None, keywords=None, limit=25):
    """Search Apollo.io for companies matching ICP criteria (people search requires Pro+ plan).
    Falls back to mixed_companies/search which is available on free/essential plans."""
    key = os.environ.get('APOLLO_API_KEY', '')
    if not key:
        return []

    headers = {'Content-Type': 'application/json', 'X-Api-Key': key}
    
    # Build org search payload
    # Map common ICP industry names to Apollo taxonomy values
    apollo_industry_map = {
        'saas': ['internet', 'information technology & services'],
        'fintech': ['financial services', 'banking'],
        'healthcare': ['hospital & health care', 'medical practice'],
        'e-commerce': ['internet', 'retail'],
        'cybersecurity': ['computer & network security', 'information technology & services'],
        'ai': ['internet', 'information technology & services'],
        'legal': ['law practice', 'legal services'],
    }
    org_filters = {}
    if industry:
        mapped = apollo_industry_map.get(industry.strip().lower())
        if mapped:
            org_filters['organization_industries'] = mapped
        else:
            # Try the raw value as-is; if it returns 0, fall back to 'internet'
            org_filters['organization_industries_raw'] = [t.strip() for t in industry.split(',')]
            org_filters['organization_industries'] = [industry.strip()]
    if location:
        org_filters['organization_locations'] = [location]
    if company_size:
        org_filters['organization_num_employees_ranges'] = [company_size]
    if keywords:
        org_filters['q_organization_keyword_tags'] = keywords.split(',')

    payload = {
        'page': 1,
        'per_page': min(limit, 25),
    }
    # Remove the raw hint before sending
    raw_industries = org_filters.pop('organization_industries_raw', None)
    payload.update(org_filters)
    
    try:
        data = _api_post(
            'https://api.apollo.io/api/v1/mixed_companies/search',
            headers,
            payload
        )
        # If first attempt returned 0 and we have raw_industries, try the 'internet' fallback
        if not data.get('organizations') and raw_industries:
            logger.warning(f'Apollo: industry "{industry}" returned 0, falling back to "internet"')
            payload['organization_industries'] = ['internet']
            data = _api_post(
                'https://api.apollo.io/api/v1/mixed_companies/search',
                headers,
                payload
            )
        leads = []
        for org in data.get('organizations', [])[:limit]:
            contacts = (org.get('contacts') or [])[:3]  # up to 3 contacts per company
            if contacts:
                for c in contacts:
                    name = (c.get('name') or '').strip()
                    first, last = '', ''
                    if ' ' in name:
                        parts = name.rsplit(' ', 1)
                        first, last = parts[0], parts[1]
                    else:
                        first = name
                    leads.append({
                        'first_name': first,
                        'last_name': last,
                        'email': c.get('email') or '',
                        'company': org.get('name', ''),
                        'job_title': c.get('title') or '',
                        'industry': org.get('industry', industry) if industry else org.get('industry', ''),
                        'location': ', '.join(filter(None, [org.get('city', ''), org.get('state', ''), org.get('country', '')])),
                        'company_size': str(org.get('estimated_num_employees') or ''),
                        'linkedin_url': c.get('linkedin_url') or org.get('linkedin_url', ''),
                        'phone': c.get('phone') or '',
                        'source': 'apollo',
                        'lead_score': 70,
                    })
            else:
                # Company-level lead (no contacts found)
                leads.append({
                    'first_name': '',
                    'last_name': '',
                    'email': '',
                    'company': org.get('name', ''),
                    'job_title': '',
                    'industry': org.get('industry', industry) if industry else org.get('industry', ''),
                    'location': ', '.join(filter(None, [org.get('city', ''), org.get('state', ''), org.get('country', '')])),
                    'company_size': str(org.get('estimated_num_employees') or ''),
                    'linkedin_url': org.get('linkedin_url', ''),
                    'phone': org.get('phone', ''),
                    'website': org.get('website_url', ''),
                    'source': 'apollo',
                    'lead_score': 50,
                })
        return leads
    except Exception as e:
        logger.error(f'Apollo search error: {e}')
        return []


# ── Hunter.io ──────────────────────────────────────────────────────────

def hunter_find_email(company, first_name=None, last_name=None, domain=None):
    """Find email addresses using Hunter.io."""
    key = os.environ.get('HUNTER_API_KEY', '')
    if not key:
        return None

    params = {'api_key': key}
    if domain:
        params['domain'] = domain
    elif company:
        params['company'] = company
    if first_name and last_name:
        params['first_name'] = first_name
        params['last_name'] = last_name

    try:
        url = 'https://api.hunter.io/v2/email-finder?' + urllib.parse.urlencode(params)
        try:
            data = _api_get(url, {})
        except:
            return None
        d = data.get('data', {})
        return {
            'email': d.get('email', ''),
            'confidence': d.get('score', 0),
            'first_name': d.get('first_name', ''),
            'last_name': d.get('last_name', ''),
            'company': d.get('company', ''),
        }
    except Exception as e:
        logger.error(f'Hunter find_email error: {e}')
        return None


def hunter_domain_search(domain, limit=10):
    """Find people at a company domain via Hunter domain search."""
    key = os.environ.get('HUNTER_API_KEY', '')
    if not key:
        return []
    
    try:
        import urllib.request, json
        url = f'https://api.hunter.io/v2/domain-search?domain={domain}&api_key={key}'
        resp = urllib.request.urlopen(url, timeout=15)
        data = json.loads(resp.read())
        emails = data.get('data', {}).get('emails', [])
        people = []
        for e in emails[:limit]:
            people.append({
                'first_name': e.get('first_name', ''),
                'last_name': e.get('last_name', ''),
                'email': e.get('value', ''),
                'position': e.get('position', e.get('department', '')),
                'source': 'hunter',
            })
        return people
    except Exception as e:
        logger.error(f'Hunter domain_search error: {e}')
        return []


# ── Serper.dev ─────────────────────────────────────────────────────────

def serper_google_search(query, num=10):
    """Search Google via Serper.dev."""
    key = os.environ.get('SERPER_API_KEY', '')
    if not key:
        return []

    try:
        data = _api_post(
            'https://google.serper.dev/search',
            {'X-API-KEY': key, 'Content-Type': 'application/json'},
            {'q': query, 'num': num}
        )
        return data.get('organic', [])
    except Exception as e:
        logger.error(f'Serper search error: {e}')
        return []


def serper_find_companies(industry, location=None, limit=10):
    """Find companies matching criteria using Serper."""
    # Query for actual companies, not listicle pages
    # First pass: look for specific job-posting companies
    queries = []
    if industry and location:
        queries.append(f'{industry} companies hiring VP Sales in {location}')
        queries.append(f'vp sales "{location}" {industry}')
    elif industry:
        queries.append(f'{industry} companies vp sales')
    queries.append(f'{industry} companies in {location} official website' if location else f'{industry} companies')
    
    seen_urls = set()
    companies = []
    
    for query in queries[:3]:
        results = serper_google_search(query, limit)
        for r in results:
            url = r.get('link', '')
            title = r.get('title', '')
            if url in seen_urls:
                continue
            seen_urls.add(url)
            
            # Skip aggregators, job boards, listicles
            skip_domains = [
    'wellfound.com', 'crunchbase.com', 'linkedin.com', 'indeed.com', 
    'glassdoor.com', 'ziprecruiter.com', 'getlatka.com', 'ycombinator.com',
    'builtinsf.com', 'topstartups.io', 'substack.com', 'medium.com',
    'wikipedia.org', 'g2.com', 'trustradius.com', 'getapp.com',
    'lensa.com', 'reddit.com', 'facebook.com', 'twitter.com', 'instagram.com',
    'youtube.com', 'zendesk.com', 'hubspot.com', 'salesforce.com',
    'forbes.com', 'techcrunch.com', 'businessinsider.com', 'bloomberg.com',
    'google.com/maps/place', 'maps.google.com',
]
            if any(x in url for x in skip_domains):
                continue

            # Skip articles, questions, job listings
            lowtitle = title.lower()
            skip_patterns = ['how to', 'what is', 'why do', 'best ', 'top ', ' guide', ' tips ', ' vs ', ' review', ' job', ' hiring']
            if any(p in lowtitle for p in skip_patterns):
                continue
                
            companies.append({
                'company': title.replace(' | LinkedIn', '').replace(' | Home', '').replace(' - Crunchbase', '').replace(' | Crunchbase', '').replace(' | Homepage', '').strip(),
                'website': url,
                'description': r.get('snippet', ''),
                'source': 'serper',
            })
            if len(companies) >= limit:
                return companies

    return companies


# ── Firecrawl ─────────────────────────────────────────────────────────

def firecrawl_scrape(url):
    """Scrape a URL with Firecrawl."""
    key = os.environ.get('FIREBALL_API_KEY', '')
    if not key:
        return None

    try:
        data = _api_post(
            'https://api.firecrawl.dev/v1/scrape',
            {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            {'url': url, 'formats': ['markdown']}
        )
        return data.get('data', {}).get('markdown', '')
    except Exception as e:
        logger.error(f'Firecrawl scrape error: {e}')
        return None


# ── Google Maps (via Serper) ──────────────────────────────────────────

def serper_places(query, num=10):
    """Search places via Serper."""
    key = os.environ.get('SERPER_API_KEY', '')
    if not key:
        return []

    try:
        data = _api_post(
            'https://google.serper.dev/places',
            {'X-API-KEY': key, 'Content-Type': 'application/json'},
            {'q': query, 'num': num}
        )
        return data.get('places', [])
    except Exception as e:
        logger.error(f'Serper places error: {e}')
        return []


def google_maps_search(industry, location, limit=10):
    """Search Google Maps via Serper for local businesses."""
    results = serper_places(f'{industry} in {location}', limit)
    businesses = []
    for p in results:
        businesses.append({
            'company': p.get('title', ''),
            'address': p.get('address', ''),
            'phone': p.get('phoneNumber', ''),
            'website': p.get('website', ''),
            'description': p.get('description', ''),
            'rating': p.get('rating', None),
            'source': 'google_maps',
        })
    return businesses


# ── Unified Hunt ───────────────────────────────────────────────────────

def run_hunt(sources, icp, limit=50):
    """Run a lead hunt across selected sources. Returns list of lead dicts."""
    leads = []
    industry = icp.get('industry', 'SaaS')
    location = icp.get('location', '')
    company_size = icp.get('companySize', '')
    title = icp.get('title', '')
    keywords = icp.get('keywords', '')

    if 'google-maps' in sources or 'google_maps' in sources:
        if location:
            maps_leads = google_maps_search(industry, location, min(limit, 10))
            for b in maps_leads:
                leads.append({
                    'first_name': '',
                    'last_name': '',
                    'email': '',
                    'company': b['company'],
                    'job_title': '',
                    'industry': industry,
                    'location': b.get('address', location),
                    'company_size': '',
                    'phone': b.get('phone', ''),
                    'website': b.get('website', ''),
                    'source': 'google_maps',
                    'lead_score': 50,
                })
            logger.info(f'Google Maps: {len(maps_leads)} businesses')

    if 'apollo' in sources:
        apollo_leads = apollo_search(
            industry=industry, location=location,
            company_size=company_size, title=title,
            keywords=keywords, limit=min(limit, 25)
        )
        leads.extend(apollo_leads)
        logger.info(f'Apollo: {len(apollo_leads)} leads')

    if 'hunter' in sources:
        # Hunter domain search - find actual people at companies
        hunter_people = []
        for l in leads[:10]:
            website = l.get('website', '')
            if website and not l.get('email'):
                # Extract domain from URL
                try:
                    domain = urllib.parse.urlparse(website).netloc or urllib.parse.urlparse('https://' + website).netloc
                    domain = domain.replace('www.', '')
                    if domain and '.' in domain:
                        people = hunter_domain_search(domain, 2)
                        for p in people:
                            p['company'] = l['company']
                            p['industry'] = industry
                            p['location'] = l.get('location', location)
                            p['source'] = 'hunter'
                            p['lead_score'] = 60
                            hunter_people.append(p)
                except:
                    pass
        
        # Also try email enrichment on existing leads
        for l in leads[:10]:
            if l.get('company') and not l.get('email'):
                result = hunter_find_email(l['company'], l.get('first_name'), l.get('last_name'))
                if result and result.get('email'):
                    l['email'] = result['email']
        
        leads.extend(hunter_people)
        logger.info(f'Hunter enrichment: {len(hunter_people)} people found via domain search')

    if 'serper' in sources:
        serper_companies = serper_find_companies(industry, location, min(limit, 10))
        for c in serper_companies:
            leads.append({
                'first_name': '',
                'last_name': '',
                'email': '',
                'company': c['company'],
                'job_title': '',
                'industry': industry,
                'location': location or '',
                'company_size': '',
                'website': c.get('website', ''),
                'source': 'serper',
                'lead_score': 40,
            })
        logger.info(f'Serper: {len(serper_companies)} companies')

    if 'firecrawl' in sources:
        for l in leads[:5]:
            if l.get('website') and not l.get('description'):
                md = firecrawl_scrape(l['website'])
                if md:
                    l['description'] = md[:500]
        logger.info('Firecrawl enrichment done')

    # Deduplicate by company+first_name
    seen = set()
    deduped = []
    for l in leads:
        key = ((l.get('company') or '').lower(), (l.get('first_name') or '').lower(), (l.get('email') or '').lower())
        if key not in seen:
            seen.add(key)
            deduped.append(l)

    return deduped[:limit]
