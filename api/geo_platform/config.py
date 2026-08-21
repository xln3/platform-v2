from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# 开发缺省 pepper 字面量。生产配置相同值（或长度不足）一律 fail-loud；
# identity/native_session.py 校验期对该旧缺省做一次性双读以完成惰性轮换。
DEFAULT_NATIVE_AUTH_PEPPER = "development-only-native-auth-pepper-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEO_", env_file=".env", extra="ignore")
    env: str = "development"
    log_level: str = "INFO"
    postgres_dsn: str = "postgresql+psycopg://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
    runtime_postgres_dsn: str = ""
    worker_postgres_dsn: str = ""
    clickhouse_url: str = "http://127.0.0.1:18123"
    clickhouse_user: str = "geo"
    clickhouse_password: str = "geo_dev_only_password"
    outbox_consumer_name: str = "clickhouse-analytics-v1"
    outbox_poll_interval_seconds: float = 1.0
    outbox_batch_size: int = 100
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "geo-platform-v2-api"
    temporal_address: str = "127.0.0.1:17233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "geo-platform-v2"
    s02_temporal_task_queue: str = "geo-platform-v2-s02"
    # Public-web acquisition and semantic/risk analysis must never consume the
    # logged-in collection worker's browser slots or account session lease.
    source_temporal_task_queue: str = "geo-platform-v2-source"
    analysis_temporal_task_queue: str = "geo-platform-v2-analysis"
    minio_endpoint: str = "http://127.0.0.1:19000"
    minio_access_key: str = "geo"
    minio_secret_key: str = "geo_dev_only_password"
    redis_url: str = "redis://127.0.0.1:16380/0"
    kms_master_key: str = "development-only-kms-master-key-change-me"
    kms_provider: str = "unavailable"
    vault_transit_address: str = ""
    vault_transit_token_file: str = ""
    vault_transit_deletion_token_file: str = ""
    vault_transit_key_name: str = ""
    bootstrap_secret: str = "development-bootstrap"
    identity_mode: str = "trusted_headers"
    native_auth_pepper: str = DEFAULT_NATIVE_AUTH_PEPPER
    native_session_hours: int = 12
    # Read-only source used once to upgrade an existing password into a native
    # V2 credential after the user successfully proves the original password.
    legacy_auth_sqlite_path: str = ""
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    oidc_algorithms: str = "RS256"
    oidc_tenant_claim: str = "https://geo.example/tenant"
    oidc_max_token_lifetime_seconds: int = 900
    oidc_clock_skew_seconds: int = 30
    oidc_authorization_endpoint: str = ""
    oidc_token_endpoint: str = ""
    oidc_client_id: str = ""
    oidc_redirect_uri: str = ""
    oidc_post_login_uri: str = "/platform/customer/"
    oidc_browser_cookie_key_file: str = ""
    terminal_task_signing_key_file: str = ""
    datasets_dir: str = ""
    # Intake AI 联网调研（Responses API + web_search；缺省值照旧版 ai_research.py env 口径）。
    research_llm_api_key: str = ""
    research_llm_model: str = "gpt-5.6-luna"
    # 国外模型统一经 inferera；不得回落到需借用宿主机翻墙代理的端点。
    research_llm_base_url: str = "https://api.inferera.com"
    research_llm_base_url_fallback: str = ""
    research_llm_max_rounds: int = 3
    # AI 调研可选模型清单（GEO_RESEARCH_LLM_MODELS，逗号分隔）：暴露给前端下拉选择；
    # 空 = 仅缺省模型可选。缺省模型（research_llm_model）恒在清单首位。
    research_llm_models: str = ""
    # AI 报告起草（reports/narrative）可选模型清单（GEO_REPORT_LLM_MODELS，逗号分隔，
    # 首项=缺省）。七项为既定选型（developlog/implementation/fix-20260807-174349.md §8），
    # 模型传输形状经 /chat/completions 逐台实测；生产网关由 research_llm_base_url 统一控制。
    report_llm_models: str = (
        "deep-deepseek-v4-flash,deep-deepseek-v4-pro,claude-opus-5,gpt-5.6-sol,"
        "gemini-3.6-flash,baidu-glm-5.2,moonshot-kimi-k3"
    )
    # CORS（GEO_CORS_ORIGINS 逗号分隔；缺省=e2e 端口 origin）。
    cors_origins: str = (
        "http://127.0.0.1:45101,http://127.0.0.1:45102,http://127.0.0.1:45103,"
        "http://127.0.0.1:45104,http://127.0.0.1:45105,http://127.0.0.1:45112"
    )
    # SiliconIndex 快照目录（只读适配；缺失→{available:false} 优雅降级）。
    siliconindex_snapshot_dir: str = "data/siliconindex-snapshots"
    # 免登录填表邀请：TTL（小时）与每邀请 AI 调用配额（ai-research/query-suggestions 共用）。
    intake_invite_ttl_hours: int = 168
    intake_invite_ai_quota: int = 3
    # collect_with_adapter 的 workflow start_to_close 预算（分钟，W1 起可配）：
    # deep_think 流远长于 normal，旧 5 分钟硬编码放不下。
    collection_activity_timeout_minutes: float = 15.0
    # 任务间拟人节奏（秒）：同一 run 内相邻采集任务之间的随机间隔 [min,max]。
    # 机器节拍（任务一完成立刻发下一问）会被豆包行为风控稳定识别出验证码
    # （2026-08-06 生产实证 25s 间隔连发撞码）；max<=0 关闭间隔。
    collection_inter_task_delay_min_s: float = 45.0
    collection_inter_task_delay_max_s: float = 150.0
    # W2 信源准确性核对 LLM（audit_run_sources）：三项留空则逐项复用
    # research_llm_*（GEO_RESEARCH_LLM_*）的值；key 缺失 → 判定口径如实落
    # llm_unavailable，绝不编造判定。key 只走本 settings，严禁入库/日志。
    audit_llm_api_key: str = ""
    audit_llm_base_url: str = ""
    audit_llm_model: str = ""
    # 主备 failover：空则复用 research_llm_base_url_fallback；再空 = 不做 failover。
    # 生产只允许 inferera；同端点不重复尝试，瞬时失败交给 activity/API 外层重试。
    audit_llm_base_url_fallback: str = ""
    # 信源帖子取证分析（post_analysis）LLM：三项留空则逐项复用 research_llm_*
    # （GEO_RESEARCH_LLM_*）的值；key 缺失 → 分析如实落 analysis_failed
    # （llm_unavailable），绝不编造标签。key 只走本 settings，严禁入库/日志。
    post_analysis_llm_api_key: str = ""
    post_analysis_llm_base_url: str = ""
    post_analysis_llm_model: str = ""
    # 主备 failover：空则复用 research_llm_base_url_fallback；再空 = 不做 failover。
    post_analysis_llm_base_url_fallback: str = ""
    # 单任务 URL 上限（API 与 service 双侧校验）、每帖联网核验 claims 上限、
    # 送 LLM 的帖子正文截断字符数。
    post_analysis_max_urls_per_task: int = 50
    post_analysis_max_claims_verified: int = 5
    post_analysis_text_char_limit: int = 30000
    version: str = "0.1.0"

    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
