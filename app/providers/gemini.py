"""Google Gemini backend (google-genai SDK).

The point of this provider is the free tier: a student with no credit card
can get an API key at https://aistudio.google.com/apikey and generate notes
at no cost (rate-limited). The SDK is imported lazily so Notra still runs
when google-genai isn't installed and the provider isn't selected.
"""

from __future__ import annotations

import time
from typing import Callable

from .. import config

MAX_TOKENS = 32000
_MAX_ATTEMPTS = 3
_RETRYABLE_CODES = {429, 500, 502, 503, 504}


def _client():
    key = config.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "No Gemini API key set. Open Settings and paste one — free keys "
            "at aistudio.google.com/apikey."
        )
    try:
        from google import genai
    except ImportError:
        raise RuntimeError(
            "The google-genai package is not installed. "
            "Run: pip3 install google-genai"
        )
    return genai.Client(api_key=key)


def credentials_available() -> bool:
    return bool(config.get("GEMINI_API_KEY"))


def _usage_dict(meta) -> dict:
    """Map Gemini usage_metadata onto the shared usage shape. Gemini's
    prompt_token_count *includes* implicitly-cached tokens, so split them out;
    thought tokens are billed as output, so fold them in."""
    prompt = getattr(meta, "prompt_token_count", 0) or 0
    cached = getattr(meta, "cached_content_token_count", 0) or 0
    out = getattr(meta, "candidates_token_count", 0) or 0
    thoughts = getattr(meta, "thoughts_token_count", 0) or 0
    return {
        "input_tokens": max(0, prompt - cached),
        "output_tokens": out + thoughts,
        "cache_creation_tokens": 0,  # implicit caching has no write charge
        "cache_read_tokens": cached,
    }


def stream(system: str, user_text: str, model: str,
           on_progress: Callable[[float], None] | None) -> tuple[str, dict]:
    client = _client()
    from google.genai import errors, types

    gen_config = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=MAX_TOKENS,
        # No thinking config: Gemini 2.5 defaults to dynamic thinking, which
        # is the right behaviour here and avoids per-model budget rules.
    )

    attempt = 0
    while True:
        attempt += 1
        chunks: list[str] = []
        received = 0
        usage = None
        try:
            for chunk in client.models.generate_content_stream(
                model=model, contents=user_text, config=gen_config,
            ):
                text = getattr(chunk, "text", None) or ""
                if text:
                    chunks.append(text)
                    received += len(text)
                    if on_progress:
                        on_progress(min(0.97, received / 22000))
                if getattr(chunk, "usage_metadata", None):
                    usage = chunk.usage_metadata  # cumulative; keep the last
            break
        except errors.APIError as exc:
            code = getattr(exc, "code", None)
            if code in _RETRYABLE_CODES and attempt < _MAX_ATTEMPTS:
                time.sleep(2 ** attempt)  # 2s, 4s
                continue
            if code == 429:
                raise RuntimeError(
                    "Gemini rate limit hit (free-tier quotas are per-minute "
                    "and per-day). Wait a bit and try again."
                ) from exc
            raise RuntimeError(f"Gemini API error: {exc}") from exc
        except ConnectionError as exc:
            if attempt < _MAX_ATTEMPTS:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Could not reach the Gemini API: {exc}") from exc

    body = "".join(chunks).strip()
    if not body:
        raise RuntimeError(
            "Gemini returned an empty response. If this repeats, the content "
            "may have tripped its safety filters — try Regenerate."
        )
    return body, _usage_dict(usage) if usage else {
        "input_tokens": 0, "output_tokens": 0,
        "cache_creation_tokens": 0, "cache_read_tokens": 0,
    }
