import os
import json


def get_groq_client():
    """Get a Groq client if API key is configured."""
    from groq import Groq
    api_key = os.environ.get('GROQ_API_KEY', '')
    if not api_key:
        return None
    return Groq(api_key=api_key)


def score_lead_via_groq(lead_data, icp_profile):
    """Score a lead's ICP match using Groq Mixtral.

    Returns dict with score (0-100), reason, and icp_match (float 0-1).
    """
    client = get_groq_client()
    if not client:
        # Fallback scoring when no API key
        return _default_score(lead_data, icp_profile)

    prompt = f"""You are a lead scoring AI. Score how well this lead matches the Ideal Customer Profile (ICP).

LEAD:
- Name: {lead_data.get('first_name', '')} {lead_data.get('last_name', '')}
- Company: {lead_data.get('company', '')}
- Job Title: {lead_data.get('job_title', '')}
- Industry: {lead_data.get('industry', '')}
- Location: {lead_data.get('location', '')}
- Company Size: {lead_data.get('company_size', '')}

ICP PROFILE:
{json.dumps(icp_profile, indent=2)}

Return ONLY valid JSON: {{"score": <integer 0-100>, "reason": "<short explanation>", "icp_match": <float 0.0-1.0>}}
"""

    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            response_format={"type": "json_object"},
            max_tokens=500,
        )
        result = json.loads(completion.choices[0].message.content)
        return {
            'score': min(100, max(0, int(result.get('score', 0)))),
            'reason': result.get('reason', ''),
            'icp_match': min(1.0, max(0.0, float(result.get('icp_match', 0)))),
        }
    except Exception as e:
        return {
            'score': 0,
            'reason': f'Scoring error: {str(e)}',
            'icp_match': 0.0,
        }


def _default_score(lead_data, icp_profile):
    """Simple fallback scoring without AI."""
    score = 0
    reasons = []

    # Industry match
    target_industries = [i.lower().strip() for i in icp_profile.get('industries', [])]
    if lead_data.get('industry', '').lower().strip() in target_industries:
        score += 25
        reasons.append('industry match')

    # Job title relevance
    target_titles = [t.lower().strip() for t in icp_profile.get('job_titles', [])]
    lead_title = lead_data.get('job_title', '').lower().strip()
    if any(t in lead_title for t in target_titles):
        score += 20
        reasons.append('title match')

    # Company size match
    size = lead_data.get('company_size', '').lower().strip()
    target_sizes = [s.lower().strip() for s in icp_profile.get('company_sizes', [])]
    if size in target_sizes:
        score += 15
        reasons.append('size match')

    # Location match
    target_locations = [l.lower().strip() for l in icp_profile.get('locations', [])]
    if lead_data.get('location', '').lower().strip() in target_locations:
        score += 10
        reasons.append('location match')

    # Seniority from job title
    seniority_keywords = ['vp', 'director', 'head of', 'chief', 'cfo', 'cto', 'ceo', 'founder', 'owner']
    if any(k in lead_title for k in seniority_keywords):
        score += 15
        reasons.append('senior role')

    score = min(100, score)
    return {
        'score': score,
        'reason': ', '.join(reasons) if reasons else 'no ICP criteria matched',
        'icp_match': score / 100.0,
    }


def enrich_lead_via_groq(lead_data):
    """Try to enrich lead data using Groq (simulated enrichment).

    Parses context from raw text / job title to infer industry, size, etc.
    """
    client = get_groq_client()
    if not client:
        return lead_data

    prompt = f"""Given this lead data, fill in any missing fields intelligently.

LEAD DATA:
{json.dumps(lead_data, indent=2)}

Return ONLY valid JSON with the same keys but filled-in values where possible.
Be conservative — don't guess if uncertain. Just return the original data if nothing to add.
"""

    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            response_format={"type": "json_object"},
            max_tokens=500,
        )
        enriched = json.loads(completion.choices[0].message.content)
        return enriched
    except Exception:
        return lead_data