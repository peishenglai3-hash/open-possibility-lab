import json
from pathlib import Path

from team_soul.config import RuntimeConfig
from team_soul.contracts import ContextPack
from team_soul.ingest import deterministic_file_event
from team_soul.privacy import (
    PrivateCorpusMapping,
    PrivacyBoundaryError,
    redact_for_remote,
    redact_payload_for_remote,
    redact_secrets,
)
from team_soul.providers import OpenAICompatibleProvider


def test_private_ingest_does_not_expose_source_locator(tmp_path: Path) -> None:
    source = tmp_path / "private-note.md"
    source.write_text("api_key=super-secret-value", encoding="utf-8")
    event = deterministic_file_event(source, private=True)
    assert event.source_locator.startswith("private:")
    assert str(source) not in event.source_locator
    assert event.metadata["privacy"] == "private"


def test_secret_redaction_and_opaque_mapping(tmp_path: Path) -> None:
    assert "super-secret-value" not in redact_secrets("api_key=super-secret-value")
    mapping = PrivateCorpusMapping(tmp_path)
    assert mapping.locator(tmp_path / "note.md").startswith("private:")
    assert mapping.actor_id(tmp_path / "note.md").startswith("private-document:")


def test_remote_provider_redacts_payload_before_send(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "safe answer"}}]}
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("team_soul.providers.urllib.request.urlopen", fake_urlopen)
    context = ContextPack(
        arm="SOUL",
        query="retrieve",
        system_contract='{"api_key":"CANARY_PLACEHOLDER_123"}',
        context_text=(
            '{"nested":{"credential":"body-secret-value"},'
            '"actor":"private-document:opaque-actor-1234",'
            '"source":"private:opaque-source-1234"}'
        ),
        selected_event_ids=["evt:one"],
    )
    provider = OpenAICompatibleProvider(
        RuntimeConfig(provider_base_url="http://provider.test", provider_model="test")
    )
    provider.generate(context)
    body = captured["body"]
    serialized = json.dumps(body, ensure_ascii=False)
    assert "system-secret-value" not in serialized
    assert "body-secret-value" not in serialized
    assert "CANARY_PLACEHOLDER_123" not in serialized
    assert "opaque-actor-1234" not in serialized
    assert "opaque-source-1234" not in serialized
    assert "[REDACTED]" in serialized
    assert context.sent_to_provider is True
    assert context.sent_event_ids == ["evt:one"]
    assert context.provider_manifest["redaction"].startswith("recursive-key")
    assert context.provider_manifest["response_received"] is True


def test_recursive_redaction_catches_structured_and_stringified_credentials() -> None:
    safe = redact_payload_for_remote(
        {
            "nested": {"api_key": "CANARY_PLACEHOLDER_123"},
            "serialized": '{"credential":"secret-value"}',
            "private_identifier": "private-document:opaque-12345678",
        }
    )
    serialized = json.dumps(safe, ensure_ascii=False)
    assert "CANARY_PLACEHOLDER_123" not in serialized
    assert "secret-value" not in serialized
    assert "opaque-12345678" not in serialized


def test_recursive_redaction_fails_closed_for_residual_secret(monkeypatch) -> None:
    monkeypatch.setattr(
        "team_soul.privacy.contains_unredacted_secret",
        lambda _text: True,
    )
    try:
        redact_payload_for_remote({"safe": "value"})
    except PrivacyBoundaryError as exc:
        assert "unredacted secret" in str(exc)
    else:
        raise AssertionError("recursive provider redaction did not fail closed")


def test_remote_redaction_fails_closed_for_residual_secret_pattern(monkeypatch) -> None:
    monkeypatch.setattr("team_soul.privacy.redact_secrets", lambda text: text)
    try:
        redact_for_remote("Authorization: Bearer abcdefghijklmnop")
    except RuntimeError as exc:
        assert "remote provider payload" in str(exc)
    else:
        raise AssertionError("residual secret was not rejected")
