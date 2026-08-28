from __future__ import annotations

from collections.abc import Callable

import pytest

from workflows.activities.deepseek_adapter import mask_proxy_url as mask_deepseek_proxy_url
from workflows.activities.doubao_adapter import mask_proxy_url as mask_doubao_proxy_url
from workflows.activities.tongyi_adapter import mask_proxy_url as mask_tongyi_proxy_url
from workflows.activities.yiyan_adapter import mask_proxy_url as mask_yiyan_proxy_url
from workflows.activities.yuanbao_adapter import mask_proxy_url as mask_yuanbao_proxy_url


@pytest.mark.parametrize(
    "mask_proxy_url",
    (
        mask_doubao_proxy_url,
        mask_deepseek_proxy_url,
        mask_tongyi_proxy_url,
        mask_yiyan_proxy_url,
        mask_yuanbao_proxy_url,
    ),
    ids=("doubao", "deepseek", "tongyi", "yiyan", "yuanbao"),
)
def test_proxy_url_masking_never_leaks_credentials(
    mask_proxy_url: Callable[[str | None], str | None],
) -> None:
    assert mask_proxy_url("http://user:pass@proxy.example.com:8080") == (
        "http://proxy.example.com:8080"
    )
    assert mask_proxy_url("http://proxy.example.com:8080") == "http://proxy.example.com:8080"
    assert mask_proxy_url(None) is None
    assert mask_proxy_url("not-a-url") == "<invalid-proxy-url>"
