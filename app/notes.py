"""Turn a lecture transcript into exam-focused LaTeX notes with Claude."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Callable

import anthropic

from . import config

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

SYSTEM_PROMPT = r"""You are an expert teaching assistant who turns raw lecture
transcripts into the notes a strong student wishes they had taken. You write
LaTeX.

## What you output

Output ONLY the body of a LaTeX document. Never emit \documentclass, \usepackage,
\begin{document}, \end{document}, a title block, or a markdown code fence. Your
first line must be a summary comment, then the notes begin:

% SUMMARY: <one sentence, plain text, max 25 words, describing the lecture>

## The document you are writing

Structure the notes in this order. Skip a section only if the lecture genuinely
has nothing for it.

1. `\section*{Overview}` — 3-5 bullets: what this lecture covered and why it
   matters in the course.
2. The main content, in `\section{...}` / `\subsection{...}` following the
   lecture's own progression. This is the bulk of the document.
3. `\section{Questions Asked in Class}` — every question a student asked, with
   the answer given, using the `qa` environment. Omit the whole section if none
   were asked.
4. `\section{Exam Focus}` — one `examfocus` environment containing an itemized,
   ranked list of what is most likely to be examined and why.
5. `\section{References}` — an enumerated list of specific, real, standard
   textbooks/papers for this topic with chapter or section numbers where you can.
   Only cite works you are confident actually exist and actually cover this
   material. Prefer canonical course texts. Never invent a title, author, DOI,
   or URL. If you are not confident about the literature for this topic, write a
   single item saying so instead of guessing.

## Environments you may use (these are the ONLY custom ones defined)

\begin{definition}{Term} ... \end{definition}
\begin{theorem}{Name} ... \end{theorem}
\begin{keypoint} ... \end{keypoint}                  % no argument
\begin{exam}{Short reason} ... \end{exam}            % a specific likely question
\begin{pitfall} ... \end{pitfall}                    % no argument
\begin{example}{Short title} ... \end{example}
\begin{derivation}{Short title} ... \end{derivation}
\begin{qa}{The question as asked} ... \end{qa}       % body = the answer given
\begin{examfocus} ... \end{examfocus}                % no argument, use once

Also available: \hl{...} to highlight a short run of PLAIN TEXT (no math, no
macros, no line breaks inside). Use it a few times per page at most, for the
single most important phrase in a passage. Standard LaTeX is fine: \textbf,
\emph, \texttt, itemize, enumerate, align, equation, tabular, booktabs rules.

## Rules that keep the document compiling

- Escape these characters in prose: & % $ # _ { } — write \&, \%, \$, \#, \_.
- All mathematics goes in $...$ or \[...\] or an align/equation environment.
  Never write a bare symbol like ≤ or α or Σ in prose — use $\leq$, $\alpha$,
  $\sum$. Do not use Unicode math or Greek characters anywhere.
- Every \begin has a matching \end. Never nest a tcolorbox environment
  (definition, theorem, keypoint, exam, pitfall, example, derivation, qa,
  examfocus) inside another one.
- No \includegraphics — there are no image files.
- No \cite or bibliography — write references as a plain enumerated list.

## How to write the content

- Reconstruct the *lecture's* argument, don't transcribe it. Merge repetitions,
  drop filler and administrative chatter ("can everyone hear me", "we'll stop
  here"), and fix obvious speech-to-text mangling of technical terms using
  context. If a term is garbled beyond recovery, write your best reading of it
  followed by \textit{[unclear]}.
- Put every definition, theorem, and worked example the lecturer gave into the
  matching environment. Reproduce derivations step by step in display math.
- When the lecturer flags something — "this will be on the exam", "this is
  important", "people always get this wrong", "remember this" — that is a
  first-class signal. Route it to an `exam`, `keypoint`, or `pitfall` box.
- Do not invent content the lecture did not cover. You may add a short piece of
  standard connective context where the transcript is thin, but never fabricate
  a formula, result, number, or claim attributed to the lecturer. If the audio
  was clearly unintelligible for a stretch, say so with \textit{[audio unclear]}
  rather than filling the gap.
- Be complete. These notes replace attending the lecture."""


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


def _build_user_message(meta: dict, transcript: str) -> str:
    header = [
        f"Course: {meta.get('course') or 'Not given'}",
        f"Lecture topic: {meta.get('topic') or 'Not given'}",
        f"Date: {meta.get('lecture_date') or 'Not given'}",
        f"Instructor: {meta.get('instructor') or 'Not given'}",
        f"Recording length: {int((meta.get('duration_sec') or 0) // 60)} minutes",
    ]
    style = config.get("NOTES_STYLE", "detailed")
    if style == "concise":
        header.append(
            "Requested style: CONCISE — tight and revision-oriented, but keep every "
            "definition, theorem, derivation, and exam signal."
        )
    else:
        header.append(
            "Requested style: DETAILED — thorough enough to replace attending the lecture."
        )

    extra = (meta.get("extra_notes") or "").strip()
    if extra:
        header.append(f"Additional instructions from the student: {extra}")

    return (
        "\n".join(header)
        + "\n\nBelow is the timestamped transcript of the lecture. Timestamps are "
        "for your orientation only — do not put them in the notes.\n\n"
        "<transcript>\n" + transcript + "\n</transcript>\n\n"
        "Write the LaTeX body for these lecture notes now."
    )


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


def _stream_text(client, model: str, system: str, messages: list[dict],
                 on_progress: Callable[[float], None] | None) -> tuple[str, dict]:
    kwargs: dict = dict(
        model=model,
        max_tokens=MAX_TOKENS,
        # The system prompt is identical on every call (every generate() and
        # every repair(), for every lecture) — cache it so repeat calls pay
        # cache-read price (roughly a tenth of input price) on these tokens
        # instead of full price every time.
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
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
            with client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    chunks.append(text)
                    received += len(text)
                    if on_progress:
                        # Typical notes land around 20k characters; ramp
                        # asymptotically so the bar keeps moving without ever
                        # claiming to be finished.
                        on_progress(min(0.97, received / 22000))
                final = stream.get_final_message()
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


def generate(meta: dict, transcript: str,
             on_progress: Callable[[float], None] | None = None) -> tuple[str, str, dict]:
    """Return (latex_body, summary, usage)."""
    if not transcript.strip():
        raise RuntimeError("The transcript is empty — nothing was recognised in the audio.")

    client = _client()
    model = config.get("CLAUDE_MODEL", "claude-opus-5")
    messages = [{"role": "user", "content": _build_user_message(meta, transcript)}]

    body, usage = _stream_text(client, model, SYSTEM_PROMPT, messages, on_progress)

    summary = ""
    match = re.search(r"^%\s*SUMMARY:\s*(.+)$", body, re.MULTILINE)
    if match:
        summary = match.group(1).strip()

    return body, summary, usage


def repair(meta: dict, broken_body: str, error_log: str) -> tuple[str, dict]:
    """One shot at fixing a body that failed to compile. Return (body, usage)."""
    client = _client()
    model = config.get("CLAUDE_MODEL", "claude-opus-5")

    prompt = (
        "The LaTeX body below failed to compile with pdflatex. Fix it and return "
        "the COMPLETE corrected body — same content, same structure, nothing "
        "dropped. Output only LaTeX, no explanation, no code fence.\n\n"
        "Common causes: an unescaped & % $ # _ { }, a Unicode symbol that should "
        "be a math macro, a missing \\end, or one boxed environment nested inside "
        "another.\n\n"
        f"<pdflatex_errors>\n{error_log[:4000]}\n</pdflatex_errors>\n\n"
        f"<body>\n{broken_body}\n</body>"
    )

    return _stream_text(
        client, model, SYSTEM_PROMPT, [{"role": "user", "content": prompt}], None
    )
