import io
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location('lead_sources', Path(__file__).parents[1] / 'app/services/lead_sources.py')
lead_sources = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lead_sources
spec.loader.exec_module(lead_sources)


class ProviderErrorTests(unittest.TestCase):
    def http_error(self, status, body, headers=None):
        return HTTPError('https://provider.example', status, 'failure', headers or {}, io.BytesIO(body))

    def test_api_get_translates_provider_credit_error_without_exposing_body(self):
        upstream = self.http_error(400, b'{"errors":[{"details":"Credits exhausted for key secret-key"}]}', {'Retry-After': '60'})
        with patch('urllib.request.urlopen', side_effect=upstream):
            with self.assertRaises(lead_sources.ProviderQuotaError) as raised:
                lead_sources._api_get('https://provider.example', {}, provider='hunter')

        error = raised.exception
        self.assertEqual(error.provider, 'hunter')
        self.assertEqual(error.upstream_status, 400)
        self.assertEqual(error.safe_message, 'API quota exhausted')
        self.assertEqual(error.retry_after, '60')
        self.assertNotIn('secret-key', str(error.to_dict()))

    def test_api_post_translates_invalid_key(self):
        upstream = self.http_error(401, b'{"message":"bad credential secret-key"}')
        with patch('urllib.request.urlopen', side_effect=upstream):
            with self.assertRaises(lead_sources.ProviderError) as raised:
                lead_sources._api_post('https://provider.example', {}, {}, provider='serper')

        self.assertEqual(raised.exception.code, 'provider_invalid_key')
        self.assertEqual(raised.exception.safe_message, 'Invalid or unauthorized API key')
        self.assertNotIn('secret-key', raised.exception.safe_message)

    @patch.object(lead_sources, 'serper_find_companies')
    @patch.object(lead_sources, 'apollo_search')
    def test_run_hunt_returns_partial_source_errors(self, apollo_search, serper_find_companies):
        apollo_search.side_effect = lead_sources.ProviderQuotaError(
            'apollo', 429, 'API quota exhausted', '30', 'provider_quota_exhausted'
        )
        serper_find_companies.return_value = [{'company': 'Example', 'website': 'https://example.com'}]

        result = lead_sources.run_hunt(['apollo', 'serper'], {'industry': 'SaaS'})

        self.assertEqual(len(result['leads']), 1)
        self.assertEqual(result['source_errors'][0]['provider'], 'apollo')
        self.assertEqual(result['source_errors'][0]['retry_after'], '30')

    @patch.object(lead_sources, 'apollo_search')
    def test_run_hunt_raises_when_every_source_is_quota_limited(self, apollo_search):
        apollo_search.side_effect = lead_sources.ProviderQuotaError(
            'apollo', 429, 'API quota exhausted', None, 'provider_quota_exhausted'
        )

        with self.assertRaises(lead_sources.ProviderQuotaError):
            lead_sources.run_hunt(['apollo'], {'industry': 'SaaS'})


if __name__ == '__main__':
    unittest.main()
