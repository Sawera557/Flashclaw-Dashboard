import os
from pathlib import Path

from dotenv import load_dotenv


# Load every local setting from the single project-level .env file.
ENV_FILE = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(ENV_FILE, override=True)


class Config:
    """Base configuration."""
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'jwt-dev-secret-change-in-production')
    GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
    JWT_ACCESS_TOKEN_EXPIRES = 86400  # 24 hours


class DevConfig(Config):
    """Development configuration."""
    DEBUG = True


class TestConfig(Config):
    """Test configuration."""
    TESTING = True


class ProdConfig(Config):
    """Production configuration."""
    DEBUG = False


config_by_name = {
    'dev': DevConfig,
    'development': DevConfig,
    'test': TestConfig,
    'testing': TestConfig,
    'prod': ProdConfig,
    'production': ProdConfig,
}
