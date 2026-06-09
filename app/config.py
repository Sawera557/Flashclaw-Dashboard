import os
from pathlib import Path

from dotenv import load_dotenv


# Load every local setting from the single project-level .env file.
ENV_FILE = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(ENV_FILE, override=True)

DEV_SECRET_KEY = 'dev-secret-key-change-in-production'
DEV_JWT_SECRET_KEY = 'jwt-dev-secret-change-in-production'
MIN_PRODUCTION_SECRET_LENGTH = 32


class Config:
    """Base configuration."""
    SECRET_KEY = os.environ.get('SECRET_KEY', DEV_SECRET_KEY)
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', DEV_JWT_SECRET_KEY)
    GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
    JWT_ACCESS_TOKEN_EXPIRES = 86400  # 24 hours

    @classmethod
    def validate(cls):
        """Validate configuration before extensions and routes are initialized."""


class DevConfig(Config):
    """Development configuration."""
    DEBUG = True


class TestConfig(Config):
    """Test configuration."""
    TESTING = True


class ProdConfig(Config):
    """Production configuration."""
    DEBUG = False

    @classmethod
    def validate(cls):
        insecure_values = {DEV_SECRET_KEY, DEV_JWT_SECRET_KEY, ''}
        invalid = [
            name for name in ('SECRET_KEY', 'JWT_SECRET_KEY')
            if getattr(cls, name, '') in insecure_values
            or len(getattr(cls, name, '')) < MIN_PRODUCTION_SECRET_LENGTH
        ]
        if invalid:
            names = ', '.join(invalid)
            raise RuntimeError(
                f'Production configuration requires secure values of at least '
                f'{MIN_PRODUCTION_SECRET_LENGTH} characters for: {names}'
            )


config_by_name = {
    'dev': DevConfig,
    'development': DevConfig,
    'test': TestConfig,
    'testing': TestConfig,
    'prod': ProdConfig,
    'production': ProdConfig,
}
