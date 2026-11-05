from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import tomllib
from typing import Any


@dataclass(frozen=True)
class RuntimeConfig:
    data_dir: Path = Path(".team-soul")
    context_budget_tokens: int = 6000
    rrf_k: int = 60
    candidate_top_n: int = 30
    provider_kind: str = "mock"
    provider_model: str = "mock-v0.1"
    provider_base_url: str = ""
    provider_api_key_env: str = "TEAM_SOUL_API_KEY"
    # Provider compatibility shim, empty by default so no provider sees an extra field.
    # Values: "" (send nothing) | "enabled" | "disabled".
    provider_thinking: str = ""
    provider_reasoning_effort: str = ""
    temperature: float = 0.0
    max_output_tokens: int = 1800
    embedding_enabled: bool = False
    embedding_model: str = ""
    closure_require_scope: bool = True
    closure_require_provenance: bool = True
    closure_require_reopen_condition: bool = True
    team_level_min_actor_coverage: int = 2
    hard_veto_blocks: bool = True
    commit_private_corpus: bool = False
    log_selected_snippets: bool = False
    redact_secrets: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "team_soul.sqlite3"


def load_config(path: str | Path | None = None) -> RuntimeConfig:
    if path is None:
        candidate = Path("team_soul.toml")
        if not candidate.exists():
            return RuntimeConfig()
        path = candidate
    config_path = Path(path)
    raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    runtime = raw.get("runtime", {})
    provider = raw.get("provider", {})
    embedding = raw.get("embedding", {})
    closure = raw.get("closure", {})
    privacy = raw.get("privacy", {})
    return RuntimeConfig(
        data_dir=Path(runtime.get("data_dir", ".team-soul")),
        context_budget_tokens=int(runtime.get("context_budget_tokens", 6000)),
        rrf_k=int(runtime.get("rrf_k", 60)),
        candidate_top_n=int(runtime.get("candidate_top_n", 30)),
        provider_kind=str(provider.get("kind", "mock")),
        provider_model=str(provider.get("model", "mock-v0.1")),
        provider_base_url=str(provider.get("base_url", "")),
        provider_api_key_env=str(provider.get("api_key_env", "TEAM_SOUL_API_KEY")),
        provider_thinking=str(provider.get("thinking", "")),
        provider_reasoning_effort=str(provider.get("reasoning_effort", "")),
        temperature=float(provider.get("temperature", 0.0)),
        max_output_tokens=int(provider.get("max_output_tokens", 1800)),
        embedding_enabled=bool(embedding.get("enabled", False)),
        embedding_model=str(embedding.get("model", "")),
        closure_require_scope=bool(closure.get("require_scope", True)),
        closure_require_provenance=bool(closure.get("require_provenance", True)),
        closure_require_reopen_condition=bool(closure.get("require_reopen_condition", True)),
        team_level_min_actor_coverage=int(closure.get("team_level_min_actor_coverage", 2)),
        hard_veto_blocks=bool(closure.get("hard_veto_blocks", True)),
        commit_private_corpus=bool(privacy.get("commit_private_corpus", False)),
        log_selected_snippets=bool(privacy.get("log_selected_snippets", False)),
        redact_secrets=bool(privacy.get("redact_secrets", True)),
        extra=raw,
    )


def provider_api_key(config: RuntimeConfig) -> str | None:
    return os.environ.get(config.provider_api_key_env)

