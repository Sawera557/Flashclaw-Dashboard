import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


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
    dedup_module.is_duplicate_lead = lambda *args, **kwargs: (False, None)

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
            'leads_under_test', Path(__file__).parents[1] / 'app/routes/leads.py'
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


leads = _load_leads_module()


class BatchDeleteAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.user = {'id': 1, 'workspace_id': 10}
        self.delete_calls = []
        self.user_patch = patch.object(leads, '_get_current_user', return_value=self.user)
        self.request_patch = patch.object(leads.request, 'get_json', return_value={'ids': [101]})
        self.delete_patch = patch.object(leads, 'delete', side_effect=self._record_delete)
        self.user_patch.start()
        self.request_patch.start()
        self.delete_patch.start()
        self.addCleanup(self.user_patch.stop)
        self.addCleanup(self.request_patch.stop)
        self.addCleanup(self.delete_patch.stop)

    def _record_delete(self, table, filters):
        self.delete_calls.append((table, filters))

    def assert_only_validated_leads_deleted(self, validated_ids):
        if not validated_ids:
            self.assertEqual(self.delete_calls, [])
            return

        self.assertEqual(len(self.delete_calls), 4)
        expected_related_filter = [('lead_id', 'in', validated_ids)]
        self.assertEqual(self.delete_calls[:3], [
            ('email_activities', expected_related_filter),
            ('lead_activities', expected_related_filter),
            ('meetings', expected_related_filter),
        ])
        self.assertEqual(self.delete_calls[3], (
            'leads',
            [('id', 'in', validated_ids), ('workspace_id', 'eq', self.user['workspace_id'])],
        ))

    def test_deletes_leads_owned_by_current_workspace(self):
        with patch.object(leads, 'select', return_value=SimpleNamespace(data=[{'id': 101}])) as select_mock:
            response = leads.batch_delete_leads()

        self.assertEqual(response, {'success': True, 'deleted': 1})
        select_mock.assert_called_once_with(
            'leads',
            columns='id',
            filters=[('id', 'in', [101]), ('workspace_id', 'eq', 10)],
        )
        self.assert_only_validated_leads_deleted([101])

    def test_does_not_delete_leads_from_another_workspace(self):
        with patch.object(leads, 'select', return_value=SimpleNamespace(data=[])):
            response = leads.batch_delete_leads()

        self.assertEqual(response, {'success': True, 'deleted': 0})
        self.assert_only_validated_leads_deleted([])

    def test_deletes_only_authorized_ids_from_mixed_workspaces(self):
        leads.request.get_json.return_value = {'ids': [101, 202, 103]}
        with patch.object(leads, 'select', return_value=SimpleNamespace(data=[{'id': 101}, {'id': 103}])):
            response = leads.batch_delete_leads()

        self.assertEqual(response, {'success': True, 'deleted': 2})
        self.assert_only_validated_leads_deleted([101, 103])


if __name__ == '__main__':
    unittest.main()
