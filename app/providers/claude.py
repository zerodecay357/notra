"""Anthropic (Claude) backend."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable

import anthropic

from .. import config

MAX_TOKENS = 32000

# Transient failures worth a retry: rate limits, connection hiccups, and the
# provider's own 5xx — never retry a 400 (bad request) or auth error, those
# won't fix themselves and would just burn more tokens for the same failure.
_RETRYABLE = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,
)
_MAX_ATTEMPTS = 3


def _has_cli_profile() -> bool:
    """True if `ant auth login` has stored a credential the SDK can pick up."""
    root = Path(os.environ.get("ANTHROPIC_CONFIG_DIR", Path.home() / ".config" / "anthropic"))
    creds = root / "credentials"
    return creds.is_dir() and any(creds.glob("*.json"))


def _client() -> anthropic.Anthropic:
    api_key = config.get("ANTHROPIC_API_KEY")
    if api_key:
        return anthropic.Anthropic(api_key=api_key)

    # No key of our own — let the SDK resolve credentials itself (env var,
    # `ant auth login` profile, workload identity). It constructs happily with
    # nothing at all, so check that something actually got resolved.
    client = anthropic.Anthropic()
    if client.api_key or getattr(client, "auth_token", None) or _has_cli_profile():
        return client

    raise RuntimeError(
        "No Anthropic credentials found. Open Settings and paste your API key "
        "(get one at console.anthropic.com)."
    )


def credentials_available() -> bool:
    try:
        _client()
        return True
    except Exception:
        return False


# Adaptive thinking and output_config.effort exist on these families only;
# sending either to an older model is a 400.
_MODERN_PREFIXES = (
    "claude-opus-5", "claude-fable-5", "claude-mythos-5",
    "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
    "claude-sonnet-5", "claude-sonnet-4-6",
)


def _supports_adaptive(model: str) -> bool:
    return model.startswith(_MODERN_PREFIXES)


def _usage_dict(usage) -> dict:
    """Pull token counts out of an Anthropic Usage object; cache fields are
    only present at all when prompt caching was actually exercised."""
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_creation_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
    }


def stream(system: str, user_text: str, model: str,
           on_progress: Callable[[float], None] | None) -> tuple[str, dict]:
    client = _client()

    kwargs: dict = dict(
        model=model,
        max_tokens=MAX_TOKENS,
        # The system prompt is identical on every call (every generate() and
        # every repair(), for every lecture) — cache it so repeat calls pay
        # cache-read price (roughly a tenth of input price) on these tokens
        # instead of full price every time.
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_text}],
    )
    if _supports_adaptive(model):
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": config.get("CLAUDE_EFFORT", "high")}

    attempt = 0
    while True:
        attempt += 1
        chunks: list[str] = []
        received = 0
        try:
            with client.messages.stream(**kwargs) as s:
                for text in s.text_stream:
                    chunks.append(text)
                    received += len(text)
                    if on_progress:
                        # Typical notes land around 20k characters; ramp
                        # asymptotically so the bar keeps moving without ever
                        # claiming to be finished.
                        on_progress(min(0.97, received / 22000))
                final = s.get_final_message()
            break
        except _RETRYABLE as exc:
            if attempt >= _MAX_ATTEMPTS:
                raise RuntimeError(
                    f"Claude API kept failing after {attempt} attempts ({exc}). "
                    "This is usually transient — try again in a minute."
                ) from exc
            time.sleep(2 ** attempt)  # 2s, 4s

    if final.stop_reason == "refusal":
        raise RuntimeError(
            "Claude declined to generate notes for this recording. "
            "Check that the audio is a lecture and try again."
        )

    body = "".join(chunks).strip()
    if not body:
        raise RuntimeError("Claude returned an empty response.")
    return body, _usage_dict(final.usage)
