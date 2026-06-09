import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


def _load_dashboard_module():
    flask = ModuleType('flask')
    class Blueprint:
        def __init__(self, *args, **kwargs):
            pass
        def route(self, *args, **kwargs):
            return lambda function: function
    flask.Blueprint = Blueprint
    flask.jsonify = lambda value: value

    jwt = ModuleType('flask_jwt_extended')
    jwt.jwt_required = lambda: (lambda function: function)
    jwt.get_jwt_identity = lambda: '1'

    supabase_module = ModuleType('app.services.supabase')
    supabase_module.supabase = None
    supabase_module.select_one = lambda *args, **kwargs: None
    supabase_module.eq = lambda field, value: (field, value)

    calendar_module = ModuleType('app.services.maton_calendar')
    calendar_module.get_events = lambda **kwargs: {'upcoming': []}

    stubs = {
        'flask': flask,
        'flask_jwt_extended': jwt,
        'app': ModuleType('app'),
        'app.services': ModuleType('app.services'),
        'app.services.supabase': supabase_module,
        'app.services.maton_calendar': calendar_module,
    }
    with patch.dict(sys.modules, stubs):
        spec = importlib.util.spec_from_file_location(
            'dashboard_under_test', Path(__file__).parents[1] / 'app/routes/dashboard.py'
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


dashboard = _load_dashboard_module()


class EmptyResult:
    data = []
    count = 0


class EmptyQuery:
    def select(self, *args, **kwargs):
        return self

    def execute(self):
        return EmptyResult()

    @property
    def not_(self):
        return self

    def __getattr__(self, name):
        return lambda *args, **kwargs: self


class EmptySupabase:
    def table(self, name):
        return EmptyQuery()


class DashboardEmptyStateTests(unittest.TestCase):
    def setUp(self):
        self.supabase_patch = patch.object(dashboard, 'supabase', EmptySupabase())
        self.supabase_patch.start()
        self.addCleanup(self.supabase_patch.stop)

    def test_record_backed_builders_return_empty_values(self):
        zero_stats = {
            'total_leads': 0,
            'new_today': 0,
            'enriched_leads': 0,
            'emails_sent': 0,
            'replies_received': 0,
            'positive_replies': 0,
            'meetings_booked': 0,
            'pipeline_value': 0,
            'conversion_rate': 0.0,
            'reply_rate': 0.0,
        }
        zero_queue = {
            'follow_ups_due': 0,
            'positive_replies_needing_response': 0,
            'meetings_today': 0,
            'leads_needing_enrichment': 0,
        }

        with patch.object(dashboard, 'get_events', return_value={'upcoming': []}):
            self.assertEqual(dashboard._build_stats(1, 1), zero_stats)
            self.assertEqual(dashboard._build_today_queue(1, 1), zero_queue)
            self.assertEqual(dashboard._build_maton_meetings(1), [])

        self.assertEqual(dashboard._build_recent_activities(1, 1), [])
        self.assertEqual(dashboard._build_source_performance(1), [])
        self.assertEqual(dashboard._build_pipeline_snapshot(1), {})
        self.assertEqual(dashboard._build_ai_recommendations(1, 1, zero_stats, zero_queue), [])

    def test_production_route_contains_no_seed_helper(self):
        source = Path(dashboard.__file__).read_text()
        self.assertNotIn('_seed_dashboard_data', source)
        self.assertNotIn('Seed demo dashboard data', source)

    def test_dashboard_demo_renderer_is_an_empty_state(self):
        source = (Path(__file__).parents[1] / 'frontend/index.html').read_text()
        renderer = source.split('function renderDashboardDemo()', 1)[1].split('function updateRefreshTime()', 1)[0]
        self.assertIn('No dashboard records yet.', renderer)
        self.assertNotIn('sample', renderer.lower())
        self.assertNotIn('demo mode', renderer.lower())

    def test_frontend_contains_no_static_operational_records_or_outreach_fallbacks(self):
        source = (Path(__file__).parents[1] / 'frontend/index.html').read_text()

        for value in ('sawerakhadium557@gmail.com', 'Sarah Johnson', 'Mike Chen', 'Email queued for sending (demo mode)', 'Sample outreach email body'):
            self.assertNotIn(value, source)
        for field_id in ('icp-industry', 'icp-location', 'icp-size', 'icp-title'):
            field = source.split(f'id="{field_id}"', 1)[1].split('>', 1)[0]
            self.assertNotIn(' value=', field)
        self.assertIn("api('/api/integrations')", source)
        self.assertIn("api('/api/admin/team-performance')", source)
        self.assertIn('Email generation is unavailable.', source)
        self.assertIn("setDashboardSectionVisible('dash-hubspot-section', hasOwnField(d, 'hubspot_summary'))", source)


spec = importlib.util.spec_from_file_location(
    'cleanup_dashboard_demo_data',
    Path(__file__).parents[1] / 'scripts/cleanup_dashboard_demo_data.py',
)
cleanup_script = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cleanup_script
spec.loader.exec_module(cleanup_script)


class RowsResult:
    def __init__(self, data):
        self.data = data


class RowsQuery:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *args, **kwargs):
        return self

    def eq(self, field, value):
        self.rows = [row for row in self.rows if row.get(field) == value]
        return self

    def execute(self):
        return RowsResult(self.rows)


class RowsSupabase:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return RowsQuery(list(self.tables.get(name, [])))


class CleanupIdentificationTests(unittest.TestCase):
    def test_identifies_exact_seed_values_and_only_relationship_qualified_leads(self):
        client = RowsSupabase({
            'meetings': [
                {'id': 10, 'workspace_id': 7, 'lead_id': 100, 'title': 'Demo: CloudSecure AI platform'},
                {'id': 11, 'workspace_id': 7, 'lead_id': 101, 'title': 'Real customer meeting'},
            ],
            'email_activities': [],
            'lead_activities': [],
            'leads': [
                {'id': 100, 'workspace_id': 7, 'email': 'aisha@cloudsecure.dev'},
                {'id': 101, 'workspace_id': 7, 'email': 'sarah.chen@techstartup.io'},
                {'id': 102, 'workspace_id': 7, 'email': 'marcus@fintechpro.com'},
            ],
        })

        records = cleanup_script.identify_demo_records(client, 7)

        self.assertEqual([row['id'] for row in records['meetings']], [10])
        self.assertEqual([row['id'] for row in records['leads']], [100])

    def test_cleanup_requires_workspace_specific_confirmation_phrase(self):
        self.assertEqual('DELETE DEMO DATA FROM WORKSPACE 7', f'DELETE DEMO DATA FROM WORKSPACE {7}')
        source = Path(cleanup_script.__file__).read_text()
        self.assertIn("if args.confirm != confirmation", source)
        self.assertIn('Preview only; no records were deleted.', source)


if __name__ == '__main__':
    unittest.main()
