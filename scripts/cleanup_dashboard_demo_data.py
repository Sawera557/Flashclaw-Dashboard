#!/usr/bin/env python3
"""Preview and remove dashboard demo records created by the former HTTP seeder.

This administrative script is intentionally not imported by the application. It
always performs a preview first and only deletes records when the caller passes
the workspace-specific confirmation phrase printed by the preview.
"""

import argparse
import json
import sys
from pathlib import Path


MEETING_TITLES = {
    'Demo: CloudSecure AI platform',
    'Discovery call — Ecommerce Scale',
    'Follow-up: HealthTech AI',
}
EMAIL_SIGNATURES = {
    ('Quick question about TechStartup.io', 'sarah.chen@techstartup.io'),
    ('Re: Quick question about TechStartup.io', 'sarah.chen@techstartup.io'),
    ('Growth opportunities for Ecommerce Scale', 'jwilson@ecommercescale.com'),
    ('Security solutions for CloudSecure', 'aisha@cloudsecure.dev'),
    ('Fintech Pro — partnership opportunity', 'marcus@fintechpro.com'),
}
LEAD_ACTIVITY_DESCRIPTIONS = {
    'Enriched Sarah Chen — added company size and industry',
    'Scored Sarah Chen at 85 — strong ICP match',
    'Changed Aisha Patel status to meeting_booked',
}
# Leads were not inserted by the former route seeder, so only remove a lead when
# it both has a relationship to a distinctive seeded child record and matches a
# distinctive demo identity value.
LEAD_IDENTITY_VALUES = {
    'sarah.chen@techstartup.io',
    'jwilson@ecommercescale.com',
    'aisha@cloudsecure.dev',
    'marcus@fintechpro.com',
    'techstartup.io',
    'ecommerce scale',
    'cloudsecure',
    'fintech pro',
    'healthtech ai',
    'sarah chen',
    'aisha patel',
}


def _data(result):
    return list(getattr(result, 'data', None) or [])


def _matches_demo_lead(lead):
    values = {
        str(lead.get('email') or '').strip().lower(),
        str(lead.get('company') or '').strip().lower(),
        f"{lead.get('first_name') or ''} {lead.get('last_name') or ''}".strip().lower(),
    }
    return bool(values & LEAD_IDENTITY_VALUES)


def identify_demo_records(client, workspace_id):
    """Return exact seeded child records and relationship-qualified demo leads."""
    def rows(table):
        return _data(client.table(table).select('*').eq('workspace_id', workspace_id).execute())

    meetings = [row for row in rows('meetings') if row.get('title') in MEETING_TITLES]
    emails = [
        row for row in rows('email_activities')
        if (row.get('subject'), row.get('recipient')) in EMAIL_SIGNATURES
    ]
    activities = [
        row for row in rows('lead_activities')
        if row.get('description') in LEAD_ACTIVITY_DESCRIPTIONS
    ]
    related_lead_ids = {
        row.get('lead_id') for row in meetings + emails + activities if row.get('lead_id') is not None
    }
    leads = [
        row for row in rows('leads')
        if row.get('id') in related_lead_ids and _matches_demo_lead(row)
    ]
    return {
        'lead_activities': activities,
        'email_activities': emails,
        'meetings': meetings,
        'leads': leads,
    }


def _delete_ids(client, table, workspace_id, records):
    ids = [row['id'] for row in records if row.get('id') is not None]
    if ids:
        client.table(table).delete().eq('workspace_id', workspace_id).in_('id', ids).execute()
    return len(ids)


def cleanup(client, workspace_id, records):
    """Delete identified records in foreign-key-safe order."""
    return {
        table: _delete_ids(client, table, workspace_id, records[table])
        for table in ('lead_activities', 'email_activities', 'meetings', 'leads')
    }


def _summary(records):
    return {
        table: {'count': len(rows), 'ids': [row.get('id') for row in rows]}
        for table, rows in records.items()
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace-id', type=int, required=True, help='Workspace to inspect')
    parser.add_argument('--confirm', help='Exact workspace-specific confirmation phrase')
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    from app.services.supabase import supabase

    if supabase is None:
        parser.error('Supabase is not configured; set SUPABASE_URL and SUPABASE_SERVICE_KEY')

    records = identify_demo_records(supabase, args.workspace_id)
    print(json.dumps(_summary(records), indent=2, sort_keys=True))

    confirmation = f'DELETE DEMO DATA FROM WORKSPACE {args.workspace_id}'
    if args.confirm is None:
        print('\nPreview only; no records were deleted.')
        print(f'To delete exactly these records, rerun with --confirm "{confirmation}"')
        return 0
    if args.confirm != confirmation:
        print(f'Confirmation mismatch. Expected exactly: {confirmation}', file=sys.stderr)
        return 2

    deleted = cleanup(supabase, args.workspace_id, records)
    print('Deleted records:')
    print(json.dumps(deleted, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
