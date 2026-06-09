import unittest

from app import create_app


class ApplicationStartupTests(unittest.TestCase):
    def test_create_app_registers_all_blueprints_with_test_configuration(self):
        application = create_app('test')

        self.assertTrue(application.config['TESTING'])
        self.assertEqual(
            set(application.blueprints),
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
