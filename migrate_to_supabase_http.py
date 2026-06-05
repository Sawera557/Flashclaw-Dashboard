#!/usr/bin/env python3
"""
Full Supabase migration: create tables + import all data via HTTP API.
Works over IPv4. No direct Postgres connection needed.

Usage:
  1. python migrate_to_supabase_http.py --print-sql  (prints SQL for table creation)
  2. Paste that SQL into Supabase SQL Editor and run it
  3. python migrate_to_supabase_http.py --import-data
     (uses SUPABASE_URL + SUPABASE_SERVICE_KEY from env or args)
"""

import json, os, sys, uuid
from datetime import datetime

SUPABASE_URL = "https://zunkrrcnaaicpqkplnzw.supabase.co"
SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp1bmtycmNuYWFpY3Bxa3Bsbnp3Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MDYyMzI0MSwiZXhwIjoyMDk2MTk5MjQxfQ.DL54OfNnoQ9qwmUfypod_r722yjoGpsx-7AuBfDhj_g"

TABLE_ORDER = [
    'workspaces', 'users', 'leads', 'linkedin_activities',
    'lead_activities', 'email_activities', 'meetings'
]

CREATE_TABLES_SQL = """
-- Drop existing tables (clean start)
DROP TABLE IF EXISTS generated_emails CASCADE;
DROP TABLE IF EXISTS hubspot_deals CASCADE;
DROP TABLE IF EXISTS api_keys CASCADE;
DROP TABLE IF EXISTS integrations CASCADE;
DROP TABLE IF EXISTS meetings CASCADE;
DROP TABLE IF EXISTS email_activities CASCADE;
DROP TABLE IF EXISTS lead_activities CASCADE;
DROP TABLE IF EXISTS linkedin_activities CASCADE;
DROP TABLE IF EXISTS leads CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS workspaces CASCADE;

-- Workspaces
CREATE TABLE workspaces (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Users
CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT REFERENCES workspaces(id),
    name VARCHAR(200) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) DEFAULT 'sdr',
    avatar VARCHAR(10),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Leads
CREATE TABLE leads (
    id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT REFERENCES workspaces(id),
    user_id BIGINT REFERENCES users(id),
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    email VARCHAR(255),
    company VARCHAR(255),
    job_title VARCHAR(255),
    phone VARCHAR(50),
    linkedin_url VARCHAR(500),
    website VARCHAR(500),
    industry VARCHAR(200),
    location VARCHAR(200),
    company_size VARCHAR(50),
    source VARCHAR(50) DEFAULT 'manual',
    lead_score INTEGER DEFAULT 0,
    status VARCHAR(30) DEFAULT 'new',
    icp_match FLOAT DEFAULT 0.0,
    score_reason TEXT,
    enriched_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- LinkedIn Activities
CREATE TABLE linkedin_activities (
    id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT REFERENCES workspaces(id),
    user_id BIGINT REFERENCES users(id),
    lead_name VARCHAR(200),
    company VARCHAR(255),
    linkedin_url VARCHAR(500),
    activity_type VARCHAR(50),
    activity_date VARCHAR(50),
    notes TEXT,
    source VARCHAR(50) DEFAULT 'manual',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Lead Activities
CREATE TABLE lead_activities (
    id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT REFERENCES workspaces(id),
    user_id BIGINT REFERENCES users(id),
    lead_id BIGINT REFERENCES leads(id),
    activity_type VARCHAR(50),
    description TEXT,
    metadata_json TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Email Activities
CREATE TABLE email_activities (
    id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT REFERENCES workspaces(id),
    user_id BIGINT REFERENCES users(id),
    lead_id BIGINT REFERENCES leads(id),
    email_type VARCHAR(20) DEFAULT 'cold',
    subject VARCHAR(500),
    recipient VARCHAR(255),
    status VARCHAR(30) DEFAULT 'draft',
    sent_at TIMESTAMPTZ,
    opened_at TIMESTAMPTZ,
    clicked_at TIMESTAMPTZ,
    replied_at TIMESTAMPTZ,
    reply_sentiment VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Meetings
CREATE TABLE meetings (
    id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT REFERENCES workspaces(id),
    user_id BIGINT REFERENCES users(id),
    lead_id BIGINT REFERENCES leads(id),
    title VARCHAR(300),
    meeting_type VARCHAR(50) DEFAULT 'discovery',
    scheduled_at TIMESTAMPTZ,
    duration_minutes INTEGER DEFAULT 30,
    status VARCHAR(20) DEFAULT 'scheduled',
    notes TEXT,
    calendar_event_id VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable Row Level Security (but allow service_role full access)
ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE linkedin_activities ENABLE ROW LEVEL SECURITY;
ALTER TABLE lead_activities ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_activities ENABLE ROW LEVEL SECURITY;
ALTER TABLE meetings ENABLE ROW LEVEL SECURITY;
"""


def print_sql():
    print("=" * 60)
    print("STEP 1: Go to https://supabase.com/dashboard/project/zunkrrcnaaicpqkplnzw/sql/new")
    print("STEP 2: Paste the SQL below into the SQL Editor")
    print("STEP 3: Click 'Run'")
    print("STEP 4: Come back here and run: python migrate_to_supabase_http.py --import-data")
    print("=" * 60)
    print()
    print(CREATE_TABLES_SQL)


def import_data():
    """Import all data from local SQLite to Supabase via REST API."""
    import httpx
    
    # Load the export
    with open('/tmp/leadhunter_export.json') as f:
        data = json.load(f)
    
    headers = {
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }
    
    client = httpx.Client(base_url=SUPABASE_URL, headers=headers, timeout=30)
    
    # DateTime columns that need proper formatting
    DT_COLS = {
        'workspaces': ['created_at'],
        'users': ['created_at'],
        'leads': ['enriched_at', 'created_at', 'updated_at'],
        'linkedin_activities': ['created_at'],
        'lead_activities': ['created_at'],
        'email_activities': ['sent_at', 'opened_at', 'clicked_at', 'replied_at', 'created_at'],
        'meetings': ['scheduled_at', 'created_at'],
    }
    
    INT_BOOL = {'is_active': None, 'sent': None}  # columns to convert from 0/1 to bool
    
    def clean_row(table, row):
        cleaned = {}
        for k, v in row.items():
            if k == 'id':
                continue
            if v is None:
                cleaned[k] = None
            elif k in DT_COLS.get(table, []) and isinstance(v, str):
                try:
                    dt = datetime.fromisoformat(v)
                    cleaned[k] = dt.isoformat()
                except:
                    cleaned[k] = v
            elif k in ('is_active', 'sent') and isinstance(v, int):
                cleaned[k] = bool(v)
            else:
                cleaned[k] = v
        return cleaned
    
    total = 0
    for table in TABLE_ORDER:
        rows = data.get(table, [])
        if not rows:
            print(f"  ⏭️  {table}: 0 rows")
            continue
        
        # Send in batches of 50 (Supabase REST limit)
        batch_size = 50
        inserted = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i+batch_size]
            cleaned = [clean_row(table, r) for r in batch]
            
            try:
                r = client.post(f"/rest/v1/{table}", json=cleaned)
                if r.status_code in (200, 201):
                    inserted += len(batch)
                else:
                    print(f"    ❌ Error inserting into {table}: {r.status_code} {r.text[:200]}")
                    # Try one by one
                    for cr in cleaned:
                        r2 = client.post(f"/rest/v1/{table}", json=[cr])
                        if r2.status_code in (200, 201):
                            inserted += 1
                        else:
                            print(f"      Failed: {r2.text[:100]}")
            except Exception as e:
                print(f"    ❌ HTTP error for {table}: {e}")
        
        print(f"  {'✅' if inserted == len(rows) else '⚠️'} {table}: {inserted}/{len(rows)} rows")
        total += inserted
    
    print(f"\n🎉 Total: {total} rows imported to Supabase!")
    
    # Verify
    print("\n--- Verification ---")
    for table in TABLE_ORDER:
        try:
            r = client.get(f"/rest/v1/{table}?select=count", headers={**headers, "Accept": "application/json"})
            # Count using a different approach
            r2 = client.get(f"/rest/v1/{table}?select=id&limit=1", headers={**headers, "Prefer": "count=exact"})
            expected = len(data.get(table, []))
            if r2.status_code == 200:
                count = int(r2.headers.get('content-range', '0-0/0').split('/')[-1])
                print(f"  {'✅' if count == expected else '❌'} {table}: {count} rows (expected {expected})")
            else:
                print(f"  ? {table}: can't verify count")
        except Exception as e:
            print(f"  ? {table}: {e}")


if __name__ == '__main__':
    if '--print-sql' in sys.argv:
        print_sql()
    elif '--import-data' in sys.argv:
        import_data()
    else:
        print("Usage:")
        print("  python migrate_to_supabase_http.py --print-sql    # Show SQL to run in Supabase editor")
        print("  python migrate_to_supabase_http.py --import-data  # Import data after tables exist")
