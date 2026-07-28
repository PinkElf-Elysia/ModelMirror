from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class OmniRouteSettings:
    enabled: bool
    base_url: str
    api_key: str
    default_router: Literal["newapi", "omniroute"]
    catalog_ttl_seconds: float = 30.0
    stale_ttl_seconds: float = 600.0
    request_timeout_seconds: float = 12.0
    budget_headers_enabled: bool = False

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.base_url and self.api_key)

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url}/v1/chat/completions"


def get_omniroute_settings() -> OmniRouteSettings:
    default_router = os.getenv("MODEL_ROUTER_DEFAULT", "newapi").strip().lower()
    if default_router not in {"newapi", "omniroute"}:
        default_router = "newapi"
    return OmniRouteSettings(
        enabled=_env_bool("OMNIROUTE_ENABLED", False),
        base_url=os.getenv("OMNIROUTE_BASE_URL", "http://omniroute:20128").strip().rstrip("/"),
        api_key=os.getenv("OMNIROUTE_API_KEY", "").strip(),
        default_router=default_router,  # type: ignore[arg-type]
        catalog_ttl_seconds=max(
            5.0,
            float(os.getenv("OMNIROUTE_CATALOG_TTL_SECONDS", "30")),
        ),
        stale_ttl_seconds=max(
            30.0,
            float(os.getenv("OMNIROUTE_STALE_TTL_SECONDS", "600")),
        ),
        request_timeout_seconds=max(
            2.0,
            float(os.getenv("OMNIROUTE_REQUEST_TIMEOUT_SECONDS", "12")),
        ),
        budget_headers_enabled=_env_bool(
            "OMNIROUTE_BUDGET_HEADERS_ENABLED",
            False,
        ),
    )
