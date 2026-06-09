import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


class FakeLeadsQuery:
    def __init__(self, store, operation='select', payload=None):
        self.store = store
        self.operation = operation
        self.payload = payload
        self.filters = []

    def select(self, *args, **kwargs):
        return self

    def insert(self, payload):
        return FakeLeadsQuery(self.store, 'insert', payload)

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def neq(self, field, value):
        self.filters.append(('neq', field, value))
        return self

    def is_(self, field, operator, value):
        self.filters.append(('is', field, operator, value))
        return self

    def execute(self):
        if self.operation == 'insert':
            row = dict(self.payload)
            row.setdefault('id', len(self.store) + 1)
            self.store.append(row)
            return SimpleNamespace(data=[row])

        rows = self.store
        for field, value in [item for item in self.filters if len(item) == 2]:
            rows = [row for row in rows if row.get(field) == value]
        return SimpleNamespace(data=[dict(row) for row in rows])


class FakeSupabase:
    def __init__(self, store):
        self.store = store
        self.queries = []

    def table(self, table):
        assert table == 'leads'
        query = FakeLeadsQuery(self.store)
        self.queries.append(query)
        return query


def _load_leads_module():
    flask = ModuleType('flask')

    class Blueprint:
        def __init__(self, *args, **kwargs):
            pass

        def route(self, *args, **kwargs):
            return lambda function: function

    flask.Blueprint = Blueprint
    flask.request = SimpleNamespace(get_json=lambda: None)
    flask.jsonify = lambda value: value
    flask.Response = object

    jwt = ModuleType('flask_jwt_extended')
    jwt.jwt_required = lambda: (lambda function: function)
    jwt.get_jwt_identity = lambda: '1'

    supabase_module = ModuleType('app.services.supabase')
    supabase_module.supabase = None
    supabase_module.select = lambda *args, **kwargs: SimpleNamespace(data=[])
    supabase_module.select_one = lambda *args, **kwargs: None
    supabase_module.insert = lambda *args, **kwargs: None
    supabase_module.update = lambda *args, **kwargs: None
    supabase_module.delete = lambda *args, **kwargs: None
    supabase_module.eq = lambda field, value: (field, 'eq', value)
    supabase_module.like = lambda field, value: (field, 'like', value)
    supabase_module.in_ = lambda field, values: (field, 'in', values)

    dedup_module = ModuleType('app.services.dedup')
    spec = importlib.util.spec_from_file_location(
        'dedup_for_workspace_tests', Path(__file__).parents[1] / 'app/services/dedup.py'
    )
    dedup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dedup)
    dedup_module.is_duplicate_lead = dedup.is_duplicate_lead

    scoring_module = ModuleType('app.services.scoring')
    scoring_module.score_lead_via_groq = lambda *args, **kwargs: None
    scoring_module.enrich_lead_via_groq = lambda *args, **kwargs: None

    stubs = {
        'flask': flask,
        'flask_jwt_extended': jwt,
        'app': ModuleType('app'),
        'app.services': ModuleType('app.services'),
        'app.services.supabase': supabase_module,
        'app.services.dedup': dedup_module,
        'app.services.scoring': scoring_module,
    }
    with patch.dict(sys.modules, stubs):
        spec = importlib.util.spec_from_file_location(
            'leads_workspace_scope_under_test', Path(__file__).parents[1] / 'app/routes/leads.py'
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


leads = _load_leads_module()


class LeadDuplicateWorkspaceScopeTests(unittest.TestCase):
    def setUp(self):
        self.store = []
        self.supabase = FakeSupabase(self.store)
        self.current_user = {'id': 1, 'workspace_id': 10}
        self.user_patch = patch.object(leads, '_get_current_user', side_effect=lambda _: self.current_user)
        self.supabase_patch = patch.object(leads, 'supabase', self.supabase)
        self.insert_patch = patch.object(leads, 'insert', side_effect=self._insert)
        self.user_patch.start()
        self.supabase_patch.start()
        self.insert_patch.start()
        self.addCleanup(self.user_patch.stop)
        self.addCleanup(self.supabase_patch.stop)
        self.addCleanup(self.insert_patch.stop)

    def _insert(self, table, data):
        return self.supabase.table(table).insert(data).execute()

    def _select_one(self, table, columns='*', filters=None):
        rows = self.store
        for field, operator, value in filters or []:
            self.assertEqual(operator, 'eq')
            rows = [row for row in rows if row.get(field) == value]
        return dict(rows[0]) if rows else None

    def test_each_workspace_can_create_same_email_without_exposing_other_workspace_lead(self):
        with patch.object(leads, 'select_one', side_effect=self._select_one):
            with patch.object(leads.request, 'get_json', return_value={
                'email': 'Shared@Example.com', 'first_name': 'Workspace One Secret'
            }):
                first_response, first_status = leads.create_lead()

            self.current_user = {'id': 2, 'workspace_id': 20}
            with patch.object(leads.request, 'get_json', return_value={
                'email': 'shared@example.com', 'first_name': 'Workspace Two'
            }):
                second_response, second_status = leads.create_lead()

            with patch.object(leads.request, 'get_json', return_value={'email': 'shared@example.com'}):
                duplicate_response, duplicate_status = leads.create_lead()

        self.assertEqual((first_status, second_status, duplicate_status), (201, 201, 409))
        self.assertEqual([row['workspace_id'] for row in self.store], [10, 20])
        self.assertEqual(second_response['lead']['workspace_id'], 20)
        self.assertEqual(duplicate_response['lead']['workspace_id'], 20)
        self.assertNotIn('Workspace One Secret', str(second_response))
        self.assertNotIn('Workspace One Secret', str(duplicate_response))

    def test_bulk_duplicate_detection_only_uses_authenticated_workspace(self):
        self.store.append({
            'id': 1, 'workspace_id': 10, 'user_id': 1,
            'email': 'shared@example.com', 'first_name': 'Workspace One Secret',
        })
        self.current_user = {'id': 2, 'workspace_id': 20}

        with patch.object(leads.request, 'get_json', return_value=[{'email': 'shared@example.com'}]):
            response, status = leads.bulk_create_leads()

        self.assertEqual((response, status), ({'saved': 1, 'errors': 0}, 201))
        self.assertEqual(self.store[-1]['workspace_id'], 20)
        self.assertIn(('workspace_id', 20), self.supabase.queries[0].filters)

    def test_hunt_persistence_duplicate_detection_only_uses_authenticated_workspace(self):
        self.store.append({
            'id': 1, 'workspace_id': 10, 'user_id': 1,
            'email': 'shared@example.com', 'first_name': 'Workspace One Secret',
        })
        self.current_user = {'id': 2, 'workspace_id': 20}
        lead_sources = ModuleType('app.services.lead_sources')
        lead_sources.ProviderError = type('ProviderError', (Exception,), {})
        lead_sources.ProviderQuotaError = type('ProviderQuotaError', (Exception,), {})
        lead_sources.run_hunt = lambda *args, **kwargs: {
            'leads': [{'email': 'shared@example.com', 'first_name': 'Workspace Two'}],
            'source_errors': [],
        }

        with patch.dict(sys.modules, {'app.services.lead_sources': lead_sources}):
            with patch.object(leads.request, 'get_json', return_value={'sources': ['test']}):
                response = leads.hunt_leads()

        self.assertEqual(response['leads_found'], 1)
        self.assertEqual(response['leads'][0]['workspace_id'], 20)
        self.assertNotIn('Workspace One Secret', str(response))
        self.assertIn(('workspace_id', 20), self.supabase.queries[0].filters)


if __name__ == '__main__':
    unittest.main()
