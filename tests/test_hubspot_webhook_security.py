import base64
import hashlib
import hmac
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


def _load_hubspot_module():
    flask = ModuleType('flask')

    class Blueprint:
        def __init__(self, *args, **kwargs):
            pass

        def route(self, *args, **kwargs):
            return lambda function: function

    flask.Blueprint = Blueprint
    flask.request = object()
    flask.jsonify = lambda value: value

    jwt = ModuleType('flask_jwt_extended')
    jwt.jwt_required = lambda: (lambda function: function)
    jwt.get_jwt_identity = lambda: '1'

    service = ModuleType('app.services.hubspot_service')
    service.HubSpotError = RuntimeError
    for name in (
        'get_owners', 'get_open_deals_by_owner', 'get_deals_for_owner',
        'search_deals_by_owner_name', 'search_owner_by_name', 'build_context_summary',
    ):
        setattr(service, name, lambda *args, **kwargs: [])

    requests = ModuleType('requests')
    requests.post = lambda *args, **kwargs: None

    stubs = {
        'flask': flask,
        'requests': requests,
        'flask_jwt_extended': jwt,
        'app': ModuleType('app'),
        'app.services': ModuleType('app.services'),
        'app.services.hubspot_service': service,
    }
    with patch.dict(sys.modules, stubs):
        spec = importlib.util.spec_from_file_location(
            'hubspot_under_test', Path(__file__).parents[1] / 'app/routes/hubspot.py'
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


hubspot = _load_hubspot_module()


class HubSpotWebhookSecurityTests(unittest.TestCase):
    secret = 'hubspot-client-secret-for-tests'
    url = 'https://dashboard.example/api/hubspot/webhook/activity'
    method = 'POST'
    timestamp = '1700000000000'
    now_ms = 1700000000000

    def signature(self, body, secret=None):
        source = self.method.encode() + self.url.encode() + body + self.timestamp.encode()
        digest = hmac.new((secret or self.secret).encode(), source, hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    def verify(self, body, signature, timestamp=None, now_ms=None):
        return hubspot._valid_hubspot_signature(
            body,
            self.method,
            self.url,
            signature,
            timestamp or self.timestamp,
            self.now_ms if now_ms is None else now_ms,
        )

    def test_accepts_valid_v3_signature(self):
        body = b'[{"objectId":123}]'
        with patch.object(hubspot, 'HUBSPOT_CLIENT_SECRET', self.secret):
            self.assertTrue(self.verify(body, self.signature(body)))

    def test_rejects_invalid_signature(self):
        body = b'[{"objectId":123}]'
        with patch.object(hubspot, 'HUBSPOT_CLIENT_SECRET', self.secret):
            self.assertFalse(self.verify(body, self.signature(body, secret='wrong-secret')))

    def test_rejects_expired_signature(self):
        body = b'[]'
        expired_now = self.now_ms + hubspot.HUBSPOT_SIGNATURE_MAX_AGE_MS + 1
        with patch.object(hubspot, 'HUBSPOT_CLIENT_SECRET', self.secret):
            self.assertFalse(self.verify(body, self.signature(body), now_ms=expired_now))

    def test_rejects_signature_when_secret_is_not_configured(self):
        with patch.object(hubspot, 'HUBSPOT_CLIENT_SECRET', ''):
            self.assertFalse(self.verify(b'[]', 'signature'))

    def test_route_validates_signature_before_forwarding_to_slack(self):
        source = (Path(__file__).parents[1] / 'app/routes/hubspot.py').read_text()
        route = source[source.index('def hubspot_webhook_activity():'):]
        self.assertLess(route.index('_valid_hubspot_signature('), route.index('_send_batched_slack(events)'))
        self.assertIn('HUBSPOT_WEBHOOK_MAX_BYTES', route)
        self.assertIn('HUBSPOT_WEBHOOK_MAX_EVENTS', route)


if __name__ == '__main__':
    unittest.main()
