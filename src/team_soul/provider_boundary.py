from __future__ import annotations

from dataclasses import replace
from typing import Any

from .config import RuntimeConfig
from .providers import make_provider


def inspect_second_provider_boundary(config: RuntimeConfig) -> dict[str, Any]:
    """Smoke-test the declared second adapter without making a network request."""

    second_config = replace(config, provider_kind="openai-compatible")
    adapter = make_provider(second_config)
    has_endpoint = bool(second_config.provider_base_url.strip())
    return {
        "primary": {
            "kind": config.provider_kind,
            "model": config.provider_model,
        },
        "second": {
            "kind": "openai-compatible",
            "adapter_class": type(adapter).__name__,
            "adapter_available": True,
            "endpoint_configured": has_endpoint,
        },
        "network_called": False,
        "execution_status": "NOT OBSERVED / OPEN GAP",
        "evidence_boundary": (
            "adapter contract smoke only; no external provider response was collected"
        ),
    }


__all__ = ["inspect_second_provider_boundary"]
