"""Turn a lecture transcript into exam-focused LaTeX notes.

Provider-agnostic: the prompt and message assembly live here, the actual API
call is delegated to the backend selected by the AI_PROVIDER setting
(app/providers/claude.py or app/providers/gemini.py).
"""

from __future__ import annotations

import re
from typing import Callable

from . import config
from .providers import claude, gemini

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


_PROVIDERS = {"anthropic": claude, "gemini": gemini}


def _provider():
    return _PROVIDERS.get(config.get("AI_PROVIDER", "anthropic"), claude)


def active_model() -> str:
    """The model string the selected provider will be called with."""
    if config.get("AI_PROVIDER", "anthropic") == "gemini":
        return config.get("GEMINI_MODEL", "gemini-2.5-flash")
    return config.get("CLAUDE_MODEL", "claude-opus-5")


def credentials_available() -> bool:
    try:
        return _provider().credentials_available()
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


def generate(meta: dict, transcript: str,
             on_progress: Callable[[float], None] | None = None) -> tuple[str, str, dict]:
    """Return (latex_body, summary, usage)."""
    if not transcript.strip():
        raise RuntimeError("The transcript is empty — nothing was recognised in the audio.")

    body, usage = _provider().stream(
        SYSTEM_PROMPT, _build_user_message(meta, transcript), active_model(), on_progress
    )

    summary = ""
    match = re.search(r"^%\s*SUMMARY:\s*(.+)$", body, re.MULTILINE)
    if match:
        summary = match.group(1).strip()

    return body, summary, usage


def repair(meta: dict, broken_body: str, error_log: str) -> tuple[str, dict]:
    """One shot at fixing a body that failed to compile. Return (body, usage)."""
    prompt = (
        "The LaTeX body below failed to compile. Fix it and return "
        "the COMPLETE corrected body — same content, same structure, nothing "
        "dropped. Output only LaTeX, no explanation, no code fence.\n\n"
        "Common causes: an unescaped & % $ # _ { }, a Unicode symbol that should "
        "be a math macro, a missing \\end, or one boxed environment nested inside "
        "another.\n\n"
        f"<latex_errors>\n{error_log[:4000]}\n</latex_errors>\n\n"
        f"<body>\n{broken_body}\n</body>"
    )

    return _provider().stream(SYSTEM_PROMPT, prompt, active_model(), None)
