from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from .config import RuntimeConfig, provider_api_key
from .contracts import ContextPack, ProviderResponse
from .hashing import sha256_text
from .hashing import canonical_json
from .privacy import redact_payload_for_remote
from .retrieval import estimate_tokens


class Provider(Protocol):
    def generate(self, context: ContextPack) -> ProviderResponse:
        ...


@dataclass
class MockProvider:
    model: str = "mock-v0.1"

    def generate(self, context: ContextPack) -> ProviderResponse:
        selected = context.selected_event_ids
        if not selected:
            answer = (
                "NO_VERIFIED_EVIDENCE: this baseline has no retrieved source events; "
                "a team-level conclusion is not supported."
            )
        elif context.arm == "SOUL":
            answer = (
                "SOUL_PROJECTION: evidence was assembled across scoped problem history, "
                "temporal context, dissent, and unresolved conditions. No team-level agreement "
                "is inferred without an explicit closure gate. Evidence="
                + ",".join(selected)
            )
        else:
            answer = (
                "RAG_PROJECTION: direct lexical evidence was retrieved. This answer remains "
                "scoped to cited events and does not establish team agreement. Evidence="
                + ",".join(selected)
            )
        return ProviderResponse(
            answer=answer,
            cited_event_ids=selected,
            output_tokens=estimate_tokens(answer),
            provider="mock",
            model=self.model,
        )


@dataclass
class OpenAICompatibleProvider:
    config: RuntimeConfig

    def build_payload(self, context: ContextPack) -> dict:
        """Build the request body, including any declared provider compatibility shim.

        A provider whose thinking mode is on by default can spend the entire
        ``max_tokens`` budget on reasoning and return an empty answer, which would make
        every arm unusable. The shim is opt-in through configuration and is *not* a
        kernel behaviour: it changes only the request body sent to this provider.
        """

        payload: dict = {
            "model": self.config.provider_model,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_output_tokens,
            "messages": [
                {"role": "system", "content": context.system_contract},
                {"role": "user", "content": context.context_text},
            ],
        }
        thinking = self.config.provider_thinking.lower()
        if thinking in {"enabled", "disabled"}:
            payload["thinking"] = {"type": thinking}
        effort = self.config.provider_reasoning_effort.lower()
        if effort in {"low", "high", "max"}:
            payload["reasoning_effort"] = effort
        return payload

    def generate(self, context: ContextPack) -> ProviderResponse:
        base_url = self.config.provider_base_url.rstrip("/")
        if not base_url:
            raise ValueError("provider.base_url is required for openai-compatible provider")
        url = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
        payload = self.build_payload(context)
        safe_payload = redact_payload_for_remote(payload)
        payload_json = json.dumps(
            safe_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        context.provider_manifest = {
            "provider": "openai-compatible",
            "model": self.config.provider_model,
            "endpoint_hash": sha256_text(url),
            "payload_hash": sha256_text(payload_json),
            "redaction": "recursive-key-and-string-sanitizer+serialized-scan",
            "sent_event_ids": list(context.selected_event_ids),
            "actor_cursor_manifest_hash": sha256_text(canonical_json(context.actor_cursor_manifest)),
            "actor_cursor_scope": context.actor_cursor_manifest.get("scope", ""),
            "thinking_mode": self.config.provider_thinking or "provider-default",
            "reasoning_effort": self.config.provider_reasoning_effort or "provider-default",
            "request_attempted": True,
            "response_received": False,
        }
        headers = {"Content-Type": "application/json"}
        api_key = provider_api_key(self.config)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(
            url,
            data=payload_json.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            # A truncated response (IncompleteRead) or a dropped socket must fail the
            # request in a reportable way, not abort the whole evaluation run. A paid
            # qualification run crashed on exactly this before the handler was widened.
            context.provider_manifest["request_attempted"] = True
            context.provider_manifest["response_received"] = False
            context.provider_manifest["transport_error"] = type(exc).__name__ + ": " + str(exc)[:200]
            raise RuntimeError(
                "provider request failed (" + type(exc).__name__ + "): " + str(exc)[:200]
            ) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        message = result["choices"][0]["message"]
        answer = message["content"]
        # Provider-reported accounting only. A reasoning/thinking field is detected but its
        # text is deliberately never read, stored, echoed or cited (V0.3 Prompt ch. VI:
        # private chain-of-thought is not provenance).
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        details = usage.get("completion_tokens_details")
        reasoning_tokens = 0
        if isinstance(details, dict):
            reasoning_tokens = int(details.get("reasoning_tokens") or 0)
        context.sent_to_provider = True
        context.sent_event_ids = list(context.selected_event_ids)
        context.provider_manifest["response_received"] = True
        context.provider_manifest["usage"] = dict(usage)
        context.provider_manifest["provider_input_tokens"] = prompt_tokens
        # Provider-billed output, which may include reasoning tokens that are not part of
        # the answer. Kept separate from ``output_tokens`` so token metrics stay
        # comparable across providers.
        context.provider_manifest["provider_billed_output_tokens"] = completion_tokens
        context.provider_manifest["provider_reasoning_tokens"] = reasoning_tokens
        context.provider_manifest["reasoning_text_present"] = any(
            key in message for key in ("reasoning_content", "reasoning")
        )
        context.provider_manifest["latency_ms"] = latency_ms
        cited = [event_id for event_id in context.selected_event_ids if event_id in answer]
        return ProviderResponse(
            answer=answer,
            cited_event_ids=cited,
            output_tokens=estimate_tokens(answer),
            provider="openai-compatible",
            model=self.config.provider_model,
            input_tokens=prompt_tokens,
            usage=dict(usage),
            latency_ms=latency_ms,
        )


def make_provider(config: RuntimeConfig) -> Provider:
    kind = config.provider_kind.lower()
    if kind == "mock":
        return MockProvider(model=config.provider_model)
    if kind in {"openai-compatible", "openai_compatible"}:
        return OpenAICompatibleProvider(config)
    raise ValueError(f"Unsupported provider kind: {config.provider_kind}")
