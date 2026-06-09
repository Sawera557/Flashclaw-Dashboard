import re
import unittest
from pathlib import Path


FRONTEND = (Path(__file__).parents[1] / 'frontend/index.html').read_text()


class HubSpotRenderingSecurityTests(unittest.TestCase):
    def test_hubspot_deal_cards_use_text_content_instead_of_inner_html(self):
        match = re.search(r'function hsRender\(\) \{(?P<body>.*?)\n\}', FRONTEND, re.DOTALL)
        self.assertIsNotNone(match, 'hsRender function must exist')
        body = match.group('body')

        self.assertIn("name.textContent = String(dl.name || '');", body)
        self.assertIn("metadata.textContent = String(dl.stage || '') + ' · ' + String(dl.modified || '');", body)
        self.assertIn("amount.textContent = '$' + String(dl.amount == null ? 0 : dl.amount);", body)
        self.assertIn('dealGrid.replaceChildren();', body)
        self.assertNotIn("document.getElementById('hs-grid').innerHTML", body)
        self.assertNotIn('escapeHtml(String(dl.', body)


if __name__ == '__main__':
    unittest.main()
