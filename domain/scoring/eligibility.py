from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_ELIGIBLE_CAPTCHA = frozenset({"not_challenged", "solved_as_human"})
_ELIGIBLE_ACCOUNT = frozenset({"self_pool", "partner_pool", "coop_supplied_under_riskcontrol"})


def measurement_eligible(provenance: Mapping[str, Any]) -> bool:
    """Framework-free extraction of the legacy INV-1 measurement eligibility invariant."""
    return bool(
        provenance.get("captcha_mode") in _ELIGIBLE_CAPTCHA
        and provenance.get("geo_source") == "observed_gb_code"
        and provenance.get("account_source") in _ELIGIBLE_ACCOUNT
        and provenance.get("rate_policy") == "pool_burn"
        and int(provenance.get("degraded_flag", 1)) == 0
        and provenance.get("observed_gb_code") not in (None, "")
    )
