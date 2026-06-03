import re


def normalize_email(email):
    """Normalize an email address to lowercase with basic validation."""
    if not email or not isinstance(email, str):
        return None
    email = email.strip().lower()
    if '@' not in email:
        return None
    return email


def extract_domain(email):
    """Extract domain from an email address."""
    email = normalize_email(email)
    if not email:
        return None
    return email.split('@')[1]


def is_duplicate_email(email, existing_emails):
    """Check if an email already exists in a list, case-insensitive."""
    normalized = normalize_email(email)
    if not normalized:
        return False
    existing_normalized = [normalize_email(e) for e in existing_emails if e]
    return normalized in existing_normalized


def is_duplicate_domain(email, existing_emails):
    """Check if the email domain already exists in existing emails."""
    domain = extract_domain(email)
    if not domain:
        return False

    for existing in existing_emails:
        existing_domain = extract_domain(existing)
        if existing_domain == domain:
            return True
    return False


def normalize_company(company):
    """Normalize company name for comparison."""
    if not company:
        return ''
    name = company.strip().lower()
    # Remove common suffixes
    name = re.sub(r'\b(inc|llc|ltd|corp|corporation|limited|company|co|group|gmbh|b.v|bv)\b\.?', '', name)
    name = re.sub(r'[^a-z0-9\s]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def is_duplicate_lead(new_lead, existing_leads):
    """Check if a new lead is a duplicate based on email or strong signals.

    Returns (is_duplicate: bool, matched_on: str).
    """
    new_email = normalize_email(new_lead.get('email', ''))
    new_domain = extract_domain(new_lead.get('email', ''))
    new_company = normalize_company(new_lead.get('company', ''))

    for existing in existing_leads:
        ext_email = normalize_email(existing.get('email', ''))
        ext_domain = extract_domain(existing.get('email', ''))
        ext_company = normalize_company(existing.get('company', ''))

        # Exact email match
        if new_email and ext_email and new_email == ext_email:
            return True, 'email'

        # Same domain + same company
        if new_domain and ext_domain and new_domain == ext_domain:
            if new_company and ext_company and new_company == ext_company:
                return True, 'domain+company'

        # Same linkedin URL
        if new_lead.get('linkedin_url') and existing.get('linkedin_url'):
            if new_lead['linkedin_url'].strip().lower() == existing['linkedin_url'].strip().lower():
                return True, 'linkedin'

    return False, ''