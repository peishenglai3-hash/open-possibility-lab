"""Provider compatibility shim regression (owner directive section C).

Failing evidence came from the real 2-case dry-run: with the provider default, thinking
mode consumed the whole max_tokens budget and 4 of 6 calls returned an EMPTY answer
(`completion_tokens == max_tokens`, `reasoning_tokens == max_tokens`, `answer_chars == 0`).
These tests pin the shim that fixes it and pin the accounting that the qualification needs,
without any network call.
"""

from __future__ import annotations

import json

import pytest

from team_soul.config import RuntimeConfig
from team_soul.contracts import ContextPack
from team_soul.providers import OpenAICompatibleProvider


def _config(**overrides) -> RuntimeConfig:
    base = dict(
        provider_kind="openai-compatible",
        provider_model="deepseek-v4-pro",
        provider_base_url="https://api.deepseek.com",
        provider_api_key_env="DEEPSEEK_API_KEY",
        max_output_tokens=1200,
    )
    base.update(overrides)
    return RuntimeConfig(**base)


def _context() -> ContextPack:
    return ContextPack(
        arm="SOUL",
        query="q",
        system_contract="system",
        context_text="evidence evt_1",
        selected_event_ids=["evt_1"],
    )


def test_default_payload_sends_no_shim_field() -> None:
    payload = OpenAICompatibleProvider(_config()).build_payload(_context())
    assert "thinking" not in payload
    assert "reasoning_effort" not in payload


def test_thinking_can_be_disabled_per_provider() -> None:
    provider = OpenAICompatibleProvider(_config(provider_thinking="disabled"))
    payload = provider.build_payload(_context())
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["model"] == "deepseek-v4-pro"
    assert payload["max_tokens"] == 1200


def test_reasoning_effort_is_forwarded_only_when_declared() -> None:
    assert "reasoning_effort" not in OpenAICompatibleProvider(_config()).build_payload(_context())
    provider = OpenAICompatibleProvider(_config(provider_reasoning_effort="low"))
    assert provider.build_payload(_context())["reasoning_effort"] == "low"


def test_unknown_shim_values_are_not_forwarded() -> None:
    provider = OpenAICompatibleProvider(
        _config(provider_thinking="maybe", provider_reasoning_effort="extreme")
    )
    payload = provider.build_payload(_context())
    assert "thinking" not in payload
    assert "reasoning_effort" not in payload


def test_config_reads_the_shim_from_toml(tmp_path) -> None:
    from team_soul.config import load_config

    config_file = tmp_path / "team_soul.toml"
    config_file.write_text(
        "[provider]\nkind = \"openai-compatible\"\nmodel = \"deepseek-v4-pro\"\n"
        "thinking = \"disabled\"\nreasoning_effort = \"low\"\n",
        encoding="utf-8",
    )
    config = load_config(config_file)
    assert config.provider_thinking == "disabled"
    assert config.provider_reasoning_effort == "low"


class _FakeResponse:
    def __init__(self, body: dict) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


def test_generate_records_usage_without_storing_reasoning_text(monkeypatch) -> None:
    body = {
        "choices": [
            {
                "message": {
                    "content": "answer citing evt_1",
                    "reasoning_content": "PRIVATE CHAIN OF THOUGHT",
                }
            }
        ],
        "usage": {
            "prompt_tokens": 372,
            "completion_tokens": 634,
            "total_tokens": 1006,
            "completion_tokens_details": {"reasoning_tokens": 583},
        },
    }
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-key")
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda request, timeout=None: _FakeResponse(body)
    )
    context = _context()
    response = OpenAICompatibleProvider(_config(provider_thinking="disabled")).generate(context)
    assert response.answer == "answer citing evt_1"
    assert response.cited_event_ids == ["evt_1"]
    assert response.input_tokens == 372
    assert response.usage["completion_tokens"] == 634
    # output_tokens stays the ANSWER length, so token metrics remain comparable with other
    # providers; the billed total lives in the manifest instead.
    assert response.output_tokens < 634
    manifest = context.provider_manifest
    assert manifest["provider_billed_output_tokens"] == 634
    assert manifest["provider_reasoning_tokens"] == 583
    assert manifest["reasoning_text_present"] is True
    assert manifest["thinking_mode"] == "disabled"
    assert "PRIVATE CHAIN OF THOUGHT" not in json.dumps(manifest)
    assert "PRIVATE CHAIN OF THOUGHT" not in json.dumps(response.model_dump(mode="json"))


def test_a_truncated_response_fails_the_request_not_the_run(monkeypatch) -> None:
    """A paid qualification run crashed here: IncompleteRead escaped the handler."""

    import http.client

    def explode(request, timeout=None):
        raise http.client.IncompleteRead(b"")

    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-key")
    monkeypatch.setattr("urllib.request.urlopen", explode)
    context = _context()
    provider = OpenAICompatibleProvider(_config(provider_thinking="enabled"))
    with pytest.raises(RuntimeError) as error:
        provider.generate(context)
    assert "IncompleteRead" in str(error.value)
    assert context.provider_manifest["response_received"] is False
    assert context.provider_manifest["transport_error"].startswith("IncompleteRead")
