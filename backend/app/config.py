from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://zhitong:zhitong@db:5432/zhitong"
    frontend_url: str = "http://localhost:5173"
    redis_url: str = "redis://redis:6379/0"
    auth_issuer: str = "http://localhost:8001"
    auth_audience: str = "zhitong-api"
    auth_client_id: str = "zhitong-web"
    auth_jwks_url: str = "http://auth-service:8001/.well-known/jwks.json"
    auth_internal_url: str = "http://auth-service:8001"
    auth_public_url: str = "http://localhost:8001"
    auth_internal_client_secret: str = "local-dev-bff-client-change-me"
    auth_access_token_max_age_seconds: int = 15 * 60
    refresh_token_max_age_seconds: int = 60 * 60 * 24 * 30
    bff_token_encryption_secret: str = "local-dev-refresh-vault-change-me"
    bff_session_cookie_name: str = "zhitong_web_session"
    cookie_secure: bool = False
    cookie_same_site: Literal["lax", "strict", "none"] = "lax"
    session_max_age_seconds: int = 60 * 60 * 24 * 7
    github_webhook_secret: str = ""
    github_allowed_owner_id: int | None = None
    github_allowed_owner_login: str = ""
    github_workspace_id: str = ""
    github_hook_id: int | None = None
    object_storage_driver: Literal["local", "s3"] = "local"
    object_storage_local_path: str = "/var/lib/commonplan/attachments"
    object_storage_local_public_url: str = "http://localhost:8000"
    object_storage_signing_secret: str = "local-dev-file-signing-change-me"
    object_storage_endpoint: str = "https://s3.amazonaws.com"
    object_storage_public_endpoint: str = "https://s3.amazonaws.com"
    object_storage_region: str = "us-east-1"
    object_storage_bucket: str = "commonplan-attachments"
    object_storage_access_key: str = "commonplan"
    object_storage_secret_key: str = "commonplan-local-secret"
    attachment_upload_max_bytes: int = 25 * 1024 * 1024
    attachment_url_ttl_seconds: int = 15 * 60
    ingestion_worker_id: str = "ingestion-worker-1"
    ingestion_max_attempts: int = 5
    event_bus_bootstrap_servers: str = "redpanda:9092"
    event_bus_topic: str = "commonplan.knowledge.v1"
    event_bus_dlq_topic: str = "commonplan.knowledge.dlq.v1"
    event_bus_consumer_group: str = "commonplan-knowledge-indexer-v1"
    event_bus_poll_timeout_seconds: float = 1.0
    event_bus_max_poll_interval_seconds: int = 30 * 60
    event_bus_security_protocol: str = "PLAINTEXT"
    event_bus_sasl_mechanism: str = "SCRAM-SHA-256"
    event_bus_sasl_username: str = ""
    event_bus_sasl_password: str = ""
    ingestion_allow_unscanned: bool = True
    ingestion_chunk_tokens: int = 300
    ingestion_chunk_overlap_tokens: int = 50
    ingestion_docling_max_pages: int = 500
    ingestion_embedding_provider: Literal[
        "local_hash", "tei", "openai", "model_gateway"
    ] = "model_gateway"
    ingestion_embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    ingestion_embedding_model_revision: str = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    ingestion_embedding_dimensions: int = 1024
    ingestion_embedding_base_url: str = "http://embedding-service"
    ingestion_embedding_batch_size: int = 3
    ingestion_embedding_timeout_seconds: float = 120
    model_gateway_base_url: str = "http://model-gateway:4000/v1"
    model_gateway_api_key: str = ""
    model_gateway_embedding_alias: str = "commonplan-embedding-v1"
    openai_api_key: str = ""

    @model_validator(mode="after")
    def validate_cookie_settings(self) -> "Settings":
        if self.cookie_same_site == "none" and not self.cookie_secure:
            raise ValueError("SameSite=None cookies require COOKIE_SECURE=true")
        if self.ingestion_embedding_dimensions < 1:
            raise ValueError("INGESTION_EMBEDDING_DIMENSIONS must be positive")
        return self

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)


settings = Settings()
