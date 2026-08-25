from pathlib import Path

import yaml
from geo_platform.config import Settings

ROOT = Path(__file__).resolve().parents[2]
PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
NO_PROXY_KEYS = ("NO_PROXY", "no_proxy")
APPROVED_LLM_GATEWAY = "https://api.inferera.com"


def test_every_production_container_explicitly_disables_host_proxy() -> None:
    compose = yaml.safe_load((ROOT / "deploy/production/compose.yaml").read_text())
    for service_name, service in compose["services"].items():
        environment = service.get("environment") or {}
        for key in PROXY_KEYS:
            assert environment.get(key) == "", f"{service_name} does not clear {key}"
        for key in NO_PROXY_KEYS:
            assert environment.get(key) == "*", f"{service_name} does not set {key}=*"


def test_application_llm_defaults_use_only_approved_gateway() -> None:
    settings = Settings(_env_file=None)
    assert settings.research_llm_base_url == APPROVED_LLM_GATEWAY
    assert settings.research_llm_base_url_fallback == ""
    assert settings.service2_analysis_llm_base_url == APPROVED_LLM_GATEWAY
    assert settings.service2_analysis_llm_base_url_fallback == ""


def test_systemd_network_policy_clears_proxy_and_pins_llm_gateway() -> None:
    policy = (ROOT / "deploy/production/geo-platform-v2-no-host-proxy.conf").read_text()
    for key in PROXY_KEYS:
        assert f'Environment="{key}="' in policy
    for key in NO_PROXY_KEYS:
        assert f'Environment="{key}=*"' in policy
    assert "aihubmix" not in policy.lower()
    for family in (
        "RESEARCH",
        "AUDIT",
        "POST_ANALYSIS",
        "SERVICE2_ANALYSIS",
        "BRANDRANK",
    ):
        assert f"GEO_{family}_LLM_BASE_URL={APPROVED_LLM_GATEWAY}" in policy
        assert f"GEO_{family}_LLM_BASE_URL_FALLBACK={APPROVED_LLM_GATEWAY}" in policy
