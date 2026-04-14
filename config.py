import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    AWS_S3_BUCKET = os.environ.get("AWS_S3_BUCKET", "emporia-mfg-logs")
    AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
    LOG_URL_EXPIRY_S = int(os.environ.get("LOG_URL_EXPIRY_S", 3600))  # presigned URL TTL


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
