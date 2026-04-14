import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
    SQLALCHEMY_TRACK_MODIFICATIONS = False


class ProductionConfig(Config):
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "postgresql://mfg_dashboard:mfg_dashboard@localhost/mfg_dashboard",
    )
    DEBUG = False


class DevelopmentConfig(Config):
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "postgresql://mfg_dashboard:mfg_dashboard@localhost/mfg_dashboard",
    )
    DEBUG = True
    SQLALCHEMY_ECHO = False
