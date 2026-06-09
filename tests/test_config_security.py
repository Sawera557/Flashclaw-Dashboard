import importlib.util
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


dotenv = ModuleType('dotenv')
dotenv.load_dotenv = lambda *args, **kwargs: None
with patch.dict('sys.modules', {'dotenv': dotenv}):
    spec = importlib.util.spec_from_file_location('config_under_test', Path(__file__).parents[1] / 'app/config.py')
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)


class ProductionConfigSecurityTests(unittest.TestCase):
    def test_rejects_known_development_secrets(self):
        with patch.object(config.ProdConfig, 'SECRET_KEY', config.DEV_SECRET_KEY), patch.object(
            config.ProdConfig, 'JWT_SECRET_KEY', config.DEV_JWT_SECRET_KEY
        ):
            with self.assertRaisesRegex(RuntimeError, 'SECRET_KEY, JWT_SECRET_KEY'):
                config.ProdConfig.validate()

    def test_rejects_short_production_secrets(self):
        with patch.object(config.ProdConfig, 'SECRET_KEY', 'short'), patch.object(
            config.ProdConfig, 'JWT_SECRET_KEY', 'also-short'
        ):
            with self.assertRaises(RuntimeError):
                config.ProdConfig.validate()

    def test_accepts_strong_production_secrets(self):
        with patch.object(config.ProdConfig, 'SECRET_KEY', 's' * 32), patch.object(
            config.ProdConfig, 'JWT_SECRET_KEY', 'j' * 32
        ):
            config.ProdConfig.validate()


if __name__ == '__main__':
    unittest.main()
