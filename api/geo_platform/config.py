from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEO_", env_file=".env", extra="ignore")
    env: str = "development"
    log_level: str = "INFO"
    postgres_dsn: str = "postgresql+psycopg://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
    clickhouse_url: str = "http://127.0.0.1:18123"
    clickhouse_user: str = "geo"
    clickhouse_password: str = "geo_dev_only_password"
    outbox_consumer_name: str = "clickhouse-analytics-v1"
    outbox_poll_interval_seconds: float = 1.0
    outbox_batch_size: int = 100
    temporal_address: str = "127.0.0.1:17233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "geo-platform-v2"
    s02_temporal_task_queue: str = "geo-platform-v2-s02"
    minio_endpoint: str = "http://127.0.0.1:19000"
    minio_access_key: str = "geo"
    minio_secret_key: str = "geo_dev_only_password"
    redis_url: str = "redis://127.0.0.1:16380/0"
    kms_master_key: str = "development-only-kms-master-key-change-me"
    bootstrap_secret: str = "development-bootstrap"
    version: str = "0.1.0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
