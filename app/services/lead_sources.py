"""Unified lead data service — Apollo.io, Hunter.io, Serper.dev, Firecrawl."""
import os
import json
import logging
import urllib.request, urllib.parse, urllib.error
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ProviderError(Exception):
    """A safe, structured failure returned by an upstream lead provider."""

    provider: str
    upstream_status: Optional[int]
    safe_message: str
    retry_after: Optional[str] = None
    code: str = 'provider_error'

    def __post_init__(self):
        super().__init__(self.safe_message)

    def to_dict(self, source=None):
        error = {
            'source': source or self.provider,
            'provider': self.provider,
            'code': self.code,
            'message': self.safe_message,
        }
        if self.upstream_status is not None:
            error['upstream_status'] = self.upstream_status
        if self.retry_after:
            error['retry_after'] = self.retry_after
        return error


class ProviderQuotaError(ProviderError):
    """An upstream provider rejected a request because quota was exhausted."""


def _safe_upstream_message(payload, default):
    """Extract a bounded provider message without returning arbitrary response content."""
    candidates = []
    if isinstance(payload, dict):
        candidates.extend(payload.get(key) for key in ('message', 'error', 'detail', 'details'))
        errors = payload.get('errors')
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            candidates.extend(errors[0].get(key) for key in ('message', 'detail', 'details'))
        elif isinstance(errors, dict):
            candidates.extend(errors.get(key) for key in ('message', 'detail', 'details'))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().replace('\n', ' ')[:240]
    return default


def _provider_http_error(provider, error):
    """Translate urllib HTTP failures to a structured, safe provider error."""
    payload = None
    try:
        raw = error.read()
        if raw:
            payload = json.loads(raw.decode('utf-8', errors='replace'))
    except (ValueError, UnicodeDecodeError, OSError):
        payload = None

    status = getattr(error, 'code', None)
    retry_after = error.headers.get('Retry-After') if getattr(error, 'headers', None) else None
    if not retry_after and isinstance(payload, dict):
        retry_after = payload.get('retry_after') or payload.get('retryAfter')
        retry_after = str(retry_after) if retry_after is not None else None
    message = _safe_upstream_message(payload, f'{provider.title()} request failed')
    normalized = message.lower()
    quota_markers = (
        'quota', 'rate limit', 'rate-limit', 'too many requests', 'credit',
        'usage limit', 'monthly limit', 'requests limit', 'insufficient balance',
    )
    if status == 429 or any(marker in normalized for marker in quota_markers):
        return ProviderQuotaError(provider, status, 'API quota exhausted', retry_after, 'provider_quota_exhausted')
    if status in (401, 403):
        return ProviderError(provider, status, 'Invalid or unauthorized API key', retry_after, 'provider_invalid_key')
    if status in (502, 503, 504) or (status is not None and status >= 500):
        return ProviderError(provider, status, 'Upstream provider is unavailable', retry_after, 'provider_unavailable')
    return ProviderError(provider, status, f'{provider.title()} request failed', retry_after, 'provider_request_failed')


def _api_post(url, headers, data_dict, timeout=15, provider='provider'):
    """POST JSON and translate upstream transport failures."""
    body = json.dumps(data_dict).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as error:
        raise _provider_http_error(provider, error) from error
    except urllib.error.URLError as error:
        raise ProviderError(provider, None, 'Upstream provider is unavailable', code='provider_unavailable') from error


def _api_get(url, headers, timeout=15, provider='provider'):
    """GET JSON and translate upstream transport failures."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as error:
        raise _provider_http_error(provider, error) from error
    except urllib.error.URLError as error:
        raise ProviderError(provider, None, 'Upstream provider is unavailable', code='provider_unavailable') from error


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
            payload, provider='apollo'
        )
        # If first attempt returned 0 and we have raw_industries, try the 'internet' fallback
        if not data.get('organizations') and raw_industries:
            logger.warning(f'Apollo: industry "{industry}" returned 0, falling back to "internet"')
            payload['organization_industries'] = ['internet']
            data = _api_post(
                'https://api.apollo.io/api/v1/mixed_companies/search',
                headers,
                payload, provider='apollo'
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
    except urllib.error.HTTPError as error:
        raise _provider_http_error('apollo', error) from error
    except ProviderError:
        raise


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
        data = _api_get(url, {}, provider='hunter')
        d = data.get('data', {})
        return {
            'email': d.get('email', ''),
            'confidence': d.get('score', 0),
            'first_name': d.get('first_name', ''),
            'last_name': d.get('last_name', ''),
            'company': d.get('company', ''),
        }
    except urllib.error.HTTPError as error:
        raise _provider_http_error('hunter', error) from error
    except ProviderError:
        raise


def hunter_domain_search(domain, limit=10):
    """Find people at a company domain via Hunter domain search."""
    key = os.environ.get('HUNTER_API_KEY', '')
    if not key:
        return []
    
    try:
        url = f'https://api.hunter.io/v2/domain-search?domain={urllib.parse.quote(domain)}&api_key={urllib.parse.quote(key)}'
        data = _api_get(url, {}, provider='hunter')
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
    except urllib.error.HTTPError as error:
        raise _provider_http_error('hunter', error) from error
    except ProviderError:
        raise


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
            {'q': query, 'num': num}, provider='serper'
        )
        return data.get('organic', [])
    except urllib.error.HTTPError as error:
        raise _provider_http_error('serper', error) from error
    except ProviderError:
        raise


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
            {'url': url, 'formats': ['markdown']}, provider='firecrawl'
        )
        return data.get('data', {}).get('markdown', '')
    except urllib.error.HTTPError as error:
        raise _provider_http_error('firecrawl', error) from error
    except ProviderError:
        raise


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
            {'q': query, 'num': num}, provider='serper'
        )
        return data.get('places', [])
    except urllib.error.HTTPError as error:
        raise _provider_http_error('serper', error) from error
    except ProviderError:
        raise


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
    """Run selected providers and return leads plus safe per-source failures."""
    leads = []
    source_errors = []
    completed_sources = set()
    industry = icp.get('industry', 'SaaS')
    location = icp.get('location', '')
    company_size = icp.get('companySize', '')
    title = icp.get('title', '')
    keywords = icp.get('keywords', '')
    selected_sources = list(dict.fromkeys('google_maps' if source == 'google-maps' else source for source in sources))

    def run_source(source, operation):
        try:
            operation()
            completed_sources.add(source)
        except ProviderError as error:
            logger.warning('%s provider failure: %s (%s)', source, error.safe_message, error.code)
            source_errors.append(error.to_dict(source=source))

    def add_google_maps():
        if not location:
            return
        maps_leads = google_maps_search(industry, location, min(limit, 10))
        for business in maps_leads:
            leads.append({
                'first_name': '', 'last_name': '', 'email': '',
                'company': business['company'], 'job_title': '', 'industry': industry,
                'location': business.get('address', location), 'company_size': '',
                'phone': business.get('phone', ''), 'website': business.get('website', ''),
                'source': 'google_maps', 'lead_score': 50,
            })
        logger.info('Google Maps: %s businesses', len(maps_leads))

    def add_apollo():
        apollo_leads = apollo_search(
            industry=industry, location=location, company_size=company_size,
            title=title, keywords=keywords, limit=min(limit, 25),
        )
        leads.extend(apollo_leads)
        logger.info('Apollo: %s leads', len(apollo_leads))

    def add_hunter():
        hunter_people = []
        for lead in leads[:10]:
            website = lead.get('website', '')
            if website and not lead.get('email'):
                domain = urllib.parse.urlparse(website).netloc or urllib.parse.urlparse('https://' + website).netloc
                domain = domain.replace('www.', '')
                if domain and '.' in domain:
                    for person in hunter_domain_search(domain, 2):
                        person['company'] = lead['company']
                        person['industry'] = industry
                        person['location'] = lead.get('location', location)
                        person['source'] = 'hunter'
                        person['lead_score'] = 60
                        hunter_people.append(person)
        for lead in leads[:10]:
            if lead.get('company') and not lead.get('email'):
                result = hunter_find_email(lead['company'], lead.get('first_name'), lead.get('last_name'))
                if result and result.get('email'):
                    lead['email'] = result['email']
        leads.extend(hunter_people)
        logger.info('Hunter enrichment: %s people found via domain search', len(hunter_people))

    def add_serper():
        companies = serper_find_companies(industry, location, min(limit, 10))
        for company in companies:
            leads.append({
                'first_name': '', 'last_name': '', 'email': '',
                'company': company['company'], 'job_title': '', 'industry': industry,
                'location': location or '', 'company_size': '',
                'website': company.get('website', ''), 'source': 'serper', 'lead_score': 40,
            })
        logger.info('Serper: %s companies', len(companies))

    def add_firecrawl():
        for lead in leads[:5]:
            if lead.get('website') and not lead.get('description'):
                markdown = firecrawl_scrape(lead['website'])
                if markdown:
                    lead['description'] = markdown[:500]
        logger.info('Firecrawl enrichment done')

    operations = {
        'google_maps': add_google_maps,
        'apollo': add_apollo,
        'hunter': add_hunter,
        'serper': add_serper,
        'firecrawl': add_firecrawl,
    }
    for source in selected_sources:
        operation = operations.get(source)
        if operation:
            run_source(source, operation)
        else:
            completed_sources.add(source)

    if selected_sources and not completed_sources and source_errors:
        errors = source_errors
        if all(error['code'] == 'provider_quota_exhausted' for error in errors):
            first = errors[0]
            raise ProviderQuotaError(
                first['provider'], first.get('upstream_status'), 'API quota exhausted',
                first.get('retry_after'), 'provider_quota_exhausted',
            )
        first = errors[0]
        raise ProviderError(
            first['provider'], first.get('upstream_status'), first['message'],
            first.get('retry_after'), first['code'],
        )

    seen = set()
    deduped = []
    for lead in leads:
        key = ((lead.get('company') or '').lower(), (lead.get('first_name') or '').lower(), (lead.get('email') or '').lower())
        if key not in seen:
            seen.add(key)
            deduped.append(lead)

    return {'leads': deduped[:limit], 'source_errors': source_errors}
