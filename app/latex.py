"""LaTeX document assembly and compilation.

Claude only ever writes the *body*. The preamble below is fixed, so the visual
identity of every set of notes is ours, not the model's — and the set of macros
it may use is a closed, known-compiling vocabulary.

Two engines are supported. **tectonic** is preferred: it's a single small
binary that fetches only the LaTeX packages a document actually uses, which is
what lets Notra ship to students without a ~4 GB TeX Live install. A system
**pdflatex** is used as the fallback when tectonic isn't around (typical dev
setup).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import binaries

PREAMBLE = r"""\documentclass[11pt,a4paper]{article}

\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{lmodern}
\usepackage[margin=2.1cm,top=2.7cm,bottom=2.5cm,headheight=15pt]{geometry}
\usepackage{amsmath,amssymb,amsfonts,mathtools}
\usepackage{graphicx}
\usepackage{xcolor}
\usepackage{microtype}
\usepackage{enumitem}
\usepackage{booktabs}
\usepackage{array}
\usepackage{longtable}
\usepackage{fancyhdr}
\usepackage{titlesec}
\usepackage{soul}
\usepackage[most]{tcolorbox}

\definecolor{ink}{HTML}{1A1A2E}
\definecolor{accent}{HTML}{1F4E79}
\definecolor{teal}{HTML}{0F6B5C}
\definecolor{amber}{HTML}{9A5B06}
\definecolor{crimson}{HTML}{9B1C1C}
\definecolor{violet}{HTML}{5B21B6}
\definecolor{slate}{HTML}{475569}
\definecolor{mist}{HTML}{F1F5F9}
\definecolor{hlyellow}{HTML}{FFF1A8}

\usepackage[colorlinks=true,linkcolor=accent,urlcolor=accent,citecolor=accent]{hyperref}

\sethlcolor{hlyellow}
\color{ink}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.55em}
\linespread{1.06}

\titleformat{\section}{\normalfont\Large\bfseries\sffamily\color{accent}}{\thesection}{0.7em}{}
\titleformat{\subsection}{\normalfont\large\bfseries\sffamily\color{slate}}{\thesubsection}{0.6em}{}
\titlespacing*{\section}{0pt}{1.5em}{0.7em}
\titlespacing*{\subsection}{0pt}{1.1em}{0.5em}

\setlist[itemize]{leftmargin=1.3em,itemsep=0.25em,topsep=0.35em}
\setlist[enumerate]{leftmargin=1.5em,itemsep=0.25em,topsep=0.35em}

\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0.4pt}
\fancyhead[L]{\small\sffamily\color{slate}\NotraCourse}
\fancyhead[R]{\small\sffamily\color{slate}\NotraDate}
\fancyfoot[C]{\small\sffamily\color{slate}\thepage}

%% ---- Boxed environments available to the note writer --------------------
\newtcolorbox{definition}[1]{breakable,enhanced,colback=teal!5,colframe=teal,
  boxrule=0.5pt,leftrule=3pt,arc=2pt,fonttitle=\bfseries\sffamily\small,
  coltitle=white,title={Definition\,---\,#1}}

\newtcolorbox{theorem}[1]{breakable,enhanced,colback=violet!5,colframe=violet,
  boxrule=0.5pt,leftrule=3pt,arc=2pt,fonttitle=\bfseries\sffamily\small,
  coltitle=white,title={Theorem\,---\,#1}}

\newtcolorbox{keypoint}{breakable,enhanced,colback=accent!6,colframe=accent,
  boxrule=0.5pt,leftrule=3pt,arc=2pt,fonttitle=\bfseries\sffamily\small,
  coltitle=white,title={Key Point}}

\newtcolorbox{exam}[1]{breakable,enhanced,colback=amber!8,colframe=amber,
  boxrule=0.5pt,leftrule=3pt,arc=2pt,fonttitle=\bfseries\sffamily\small,
  coltitle=white,title={Likely in Exam\,---\,#1}}

\newtcolorbox{pitfall}{breakable,enhanced,colback=crimson!5,colframe=crimson,
  boxrule=0.5pt,leftrule=3pt,arc=2pt,fonttitle=\bfseries\sffamily\small,
  coltitle=white,title={Common Pitfall}}

\newtcolorbox{example}[1]{breakable,enhanced,colback=mist,colframe=slate,
  boxrule=0.5pt,leftrule=3pt,arc=2pt,fonttitle=\bfseries\sffamily\small,
  coltitle=white,title={Example\,---\,#1}}

\newtcolorbox{derivation}[1]{breakable,enhanced,colback=white,colframe=slate!60,
  boxrule=0.5pt,arc=2pt,fonttitle=\bfseries\sffamily\small,
  coltitle=slate,colbacktitle=mist,title={Derivation\,---\,#1}}

\newtcolorbox{qa}[1]{breakable,enhanced,colback=teal!4,colframe=teal!70,
  boxrule=0.5pt,arc=2pt,fonttitle=\bfseries\sffamily\small,
  coltitle=white,colbacktitle=teal!80,title={Asked in class: #1}}

\newtcolorbox{examfocus}{breakable,enhanced,colback=amber!10,colframe=amber,
  boxrule=1pt,arc=3pt,fonttitle=\bfseries\sffamily,
  coltitle=white,title={Exam Focus \textemdash{} Revise These First}}

%% Title block
\newcommand{\notratitle}{%
\begin{tcolorbox}[enhanced,colback=accent,colframe=accent,arc=3pt,boxrule=0pt,
  left=14pt,right=14pt,top=12pt,bottom=12pt]
  {\sffamily\bfseries\LARGE\color{white}\NotraTopic}\\[4pt]
  {\sffamily\color{white!85}\large \NotraCourse}\\[2pt]
  {\sffamily\color{white!70}\small \NotraDate \NotraInstructorLine}
\end{tcolorbox}
\vspace{0.6em}%
}
"""


def _tex_escape(text: str) -> str:
    """Escape a plain string for use inside LaTeX (metadata only, not body)."""
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(ch, ch) for ch in text)


# Characters Claude may emit that pdflatex's default encoding cannot render.
_UNICODE_MAP = {
    "‘": "`", "’": "'", "“": "``", "”": "''",
    "–": "--", "—": "---", "…": r"\ldots{}",
    " ": " ", " ": " ", " ": " ", "​": "",
    "×": r"$\times$", "÷": r"$\div$", "±": r"$\pm$",
    "−": "-", "≤": r"$\leq$", "≥": r"$\geq$",
    "≠": r"$\neq$", "≈": r"$\approx$", "∞": r"$\infty$",
    "→": r"$\rightarrow$", "←": r"$\leftarrow$",
    "⇒": r"$\Rightarrow$", "⇔": r"$\Leftrightarrow$",
    "∑": r"$\sum$", "∏": r"$\prod$", "√": r"$\sqrt{\phantom{x}}$",
    "∂": r"$\partial$", "∇": r"$\nabla$", "∫": r"$\int$",
    "∈": r"$\in$", "⊆": r"$\subseteq$", "⊂": r"$\subset$",
    "∪": r"$\cup$", "∩": r"$\cap$", "∅": r"$\emptyset$",
    "∀": r"$\forall$", "∃": r"$\exists$", "¬": r"$\neg$",
    "°": r"$^\circ$", "·": r"$\cdot$", "•": r"$\bullet$",
    "′": r"$'$", "½": r"$\tfrac{1}{2}$", "⅓": r"$\tfrac{1}{3}$",
    "¼": r"$\tfrac{1}{4}$", "¾": r"$\tfrac{3}{4}$",
    "≡": r"$\equiv$", "∼": r"$\sim$", "∝": r"$\propto$",
    "⊕": r"$\oplus$", "⊗": r"$\otimes$", "⋅": r"$\cdot$",
    "ℓ": r"$\ell$", "ℏ": r"$\hbar$", "ℵ": r"$\aleph$",
    "²": r"$^2$", "³": r"$^3$", "¹": r"$^1$",
    "€": r"\texteuro{}", "£": r"\pounds{}", "™": r"\texttrademark{}",
    "®": r"\textregistered{}", "©": r"\textcopyright{}",
    "✓": r"$\checkmark$", "✗": r"$\times$",
}

_GREEK = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta",
    "ε": "varepsilon", "ζ": "zeta", "η": "eta", "θ": "theta",
    "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu",
    "ν": "nu", "ξ": "xi", "π": "pi", "ρ": "rho",
    "σ": "sigma", "τ": "tau", "υ": "upsilon", "φ": "varphi",
    "χ": "chi", "ψ": "psi", "ω": "omega",
    "Γ": "Gamma", "Δ": "Delta", "Θ": "Theta", "Λ": "Lambda",
    "Ξ": "Xi", "Π": "Pi", "Σ": "Sigma", "Φ": "Phi",
    "Ψ": "Psi", "Ω": "Omega",
}
for _ch, _name in _GREEK.items():
    _UNICODE_MAP[_ch] = f"$\\{_name}$"


def sanitize_body(body: str) -> str:
    """Strip fences/preamble leakage and replace unsupported Unicode."""
    text = body.strip()

    # Drop a markdown code fence if the model wrapped the body in one.
    fence = re.match(r"^```(?:latex|tex)?\s*\n(.*?)\n?```\s*$", text, re.DOTALL)
    if fence:
        text = fence.group(1)

    # If a full document slipped through, keep only what's between the markers.
    if r"\begin{document}" in text:
        text = text.split(r"\begin{document}", 1)[1]
    if r"\end{document}" in text:
        text = text.split(r"\end{document}", 1)[0]

    for bad, good in _UNICODE_MAP.items():
        text = text.replace(bad, good)

    # Anything still outside Latin-1 would crash pdflatex — drop it.
    text = "".join(ch for ch in text if ord(ch) < 0x100 or ch in "\n\t")

    return text.strip()


def build_document(meta: dict, body: str) -> str:
    course = _tex_escape(meta.get("course") or "Lecture Notes")
    topic = _tex_escape(meta.get("topic") or "Untitled Lecture")
    date = _tex_escape(meta.get("lecture_date") or "")
    instructor = meta.get("instructor") or ""
    instructor_line = (
        r"\quad\textbullet\quad " + _tex_escape(instructor) if instructor.strip() else ""
    )

    defs = "\n".join(
        [
            r"\newcommand{\NotraCourse}{%s}" % course,
            r"\newcommand{\NotraTopic}{%s}" % topic,
            r"\newcommand{\NotraDate}{%s}" % date,
            r"\newcommand{\NotraInstructorLine}{%s}" % instructor_line,
        ]
    )

    return "\n".join(
        [
            PREAMBLE,
            defs,
            r"\begin{document}",
            r"\notratitle",
            sanitize_body(body),
            r"\end{document}",
            "",
        ]
    )


def engine() -> tuple[str, str] | None:
    """(name, path) of the LaTeX engine to use — tectonic preferred — or None."""
    for name in ("tectonic", "pdflatex"):
        path = binaries.find(name)
        if path:
            return name, path
    return None


def engine_name() -> str:
    """'tectonic', 'pdflatex', or '' when no engine is available."""
    found = engine()
    return found[0] if found else ""


def pdflatex_available() -> bool:
    """Kept for compatibility: True when *any* LaTeX engine is available."""
    return engine() is not None


def _error_tail(log: str) -> str:
    errors = [ln for ln in log.splitlines() if re.search(r"^(.*:\d+:|!|error[:\s])", ln, re.IGNORECASE)]
    return "\n".join(errors[-25:]) or "\n".join(log.splitlines()[-40:])


def compile_pdf(workdir: Path, tex_name: str = "notes.tex") -> tuple[bool, str]:
    """Compile tex_name in workdir. Returns (ok, log_tail)."""
    found = engine()
    if not found:
        return False, (
            "No LaTeX engine found. Install tectonic (recommended, single binary: "
            "https://tectonic-typesetting.github.io) or TeX Live's pdflatex."
        )
    name, path = found

    pdf_path = workdir / tex_name.replace(".tex", ".pdf")
    if pdf_path.exists():
        pdf_path.unlink()

    if name == "tectonic":
        # Tectonic reruns passes itself and downloads missing packages on the
        # fly — one invocation, but a generous timeout for that first fetch.
        proc = subprocess.run(
            [path, "--chatter", "minimal", tex_name],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=600,
        )
        log = (proc.stderr or "") + "\n" + (proc.stdout or "")
    else:
        # pdflatex needs a second pass for page references.
        log = ""
        for _ in range(2):
            proc = subprocess.run(
                [path, "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", tex_name],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=180,
            )
            log = proc.stdout or ""
            if proc.returncode != 0:
                break

    if pdf_path.exists() and pdf_path.stat().st_size > 0:
        return True, ""
    return False, _error_tail(log)
