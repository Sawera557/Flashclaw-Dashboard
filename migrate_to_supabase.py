#!/usr/bin/env python3
"""
Migrate SQLite data to Supabase (PostgreSQL).
Usage:
  python migrate_to_supabase.py "postgresql://postgres:PW@db.REF.supabase.co:5432/postgres"
"""

import json
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db
from app.models import *

DB_URL = sys.argv[1] if len(sys.argv) > 1 else None
if not DB_URL:
    print("ERROR: Provide your Supabase connection string")
    sys.exit(1)

with open('/tmp/leadhunter_export.json') as f:
    data = json.load(f)

os.environ['DATABASE_URL'] = DB_URL
os.environ['FLASK_ENV'] = 'dev'

app = create_app('dev')

# Known datetime columns per table (these need to be parsed from string)
DATETIME_COLS = {
    'workspaces': ['created_at'],
    'users': ['created_at'],
    'leads': ['enriched_at', 'created_at', 'updated_at'],
    'linkedin_activities': ['created_at'],
    'lead_activities': ['created_at'],
    'email_activities': ['sent_at', 'opened_at', 'clicked_at', 'replied_at', 'created_at'],
    'meetings': ['scheduled_at', 'created_at'],
}

model_map = {
    'workspaces': Workspace,
    'users': User,
    'leads': Lead,
    'linkedin_activities': LinkedInActivity,
    'lead_activities': LeadActivity,
    'email_activities': EmailActivity,
    'meetings': Meeting,
}

table_order = ['workspaces', 'users', 'leads', 'linkedin_activities',
               'lead_activities', 'email_activities', 'meetings']

with app.app_context():
    db.drop_all()
    db.create_all()
    print("✅ Tables created in Supabase")

    for table_name in table_order:
        model = model_map.get(table_name)
        if not model:
            continue
        rows = data.get(table_name, [])
        if not rows:
            print(f"  ⏭️  {table_name}: 0 rows")
            continue

        dt_cols = DATETIME_COLS.get(table_name, [])

        for row in rows:
            cleaned = {}
            for k, v in row.items():
                if k == 'id':
                    continue  # auto-increment
                if v is None:
                    cleaned[k] = None
                elif k in dt_cols and isinstance(v, str):
                    # Parse ISO datetime strings
                    try:
                        cleaned[k] = datetime.fromisoformat(v)
                    except (ValueError, TypeError):
                        cleaned[k] = v
                elif k == 'is_active' and isinstance(v, int):
                    cleaned[k] = bool(v)
                elif k == 'sent' and isinstance(v, int):
                    cleaned[k] = bool(v)
                else:
                    cleaned[k] = v
            try:
                obj = model(**cleaned)
                db.session.add(obj)
            except Exception as e:
                print(f"    ❌ {table_name}: {e}")
                print(f"    Row: {json.dumps(cleaned, default=str)[:200]}")

        db.session.commit()
        print(f"  ✅ {table_name}: {len(rows)} rows")

    # Reset sequences
    for table_name in table_order:
        tbl = table_name
        try:
            db.session.execute(db.text(
                f"SELECT setval(pg_get_serial_sequence('{tbl}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {tbl}), 1), false)"
            ))
        except Exception as e:
            print(f"  ⚠️  Could not reset {tbl} seq: {e}")
    db.session.commit()

    # Verify
    print("\n--- Verification ---")
    ok = True
    for table_name in table_order:
        model = model_map.get(table_name)
        if not model:
            continue
        count = model.query.count()
        expected = len(data.get(table_name, []))
        if count == expected:
            print(f"  ✅ {table_name}: {count} rows")
        else:
            print(f"  ❌ {table_name}: {count} rows (expected {expected})")
            ok = False
    if ok:
        print("\n🎉 All data migrated successfully!")
    else:
        print("\n⚠️ Some tables have mismatched counts")
