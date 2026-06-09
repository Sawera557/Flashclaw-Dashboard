"""Smoke tests for application startup."""

import unittest

from app import create_app


class AppStartupTests(unittest.TestCase):
    def test_create_app_registers_expected_blueprints(self):
        app = create_app('test')

        self.assertTrue(app.config['TESTING'])
        self.assertEqual(
            set(app.blueprints),
            {
                'activity',
                'admin',
                'ai',
                'analytics',
                'auth',
                'dashboard',
                'gmail',
                'hubspot',
                'leads',
                'linkedin',
                'meetings',
            },
        )


if __name__ == '__main__':
    unittest.main()
