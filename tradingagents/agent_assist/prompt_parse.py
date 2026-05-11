"""Classify a natural-language prompt into ticker(s) + intent.

Used by the orchestrator to decide whether to run the shortlist (screen)
stage or skip directly to deep analysis on explicitly named ticker(s).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal


# Match plain symbols and exchange-qualified forms.
# - 1-5 letter all-caps base
# - optional .X / -X suffix (BRK.B, RY.TO; CSV uses BRK-B)
_TICKER_RE = re.compile(r"\b[A-Z]{1,5}(?:[.\-][A-Z]{1,2})?\b")

_HOLD_RE = re.compile(
    r"\b(?:sell|wait|hold|holding|dump|keep|cut|trim)\b|add to",
    re.IGNORECASE,
)


Intent = Literal["single", "multi", "screen"]


@dataclass(frozen=True)
class ParsedPrompt:
    tickers: tuple[str, ...]
    intent: Intent
    hold_intent: bool


def parse_prompt(prompt: str, universe: Iterable[str]) -> ParsedPrompt:
    """Classify ``prompt`` against the known ticker ``universe``.

    Tickers are recognised only when they appear in the universe, which
    eliminates false positives like USA/ETF/BUY. Exchange-qualified forms
    using either ``.`` or ``-`` separators are normalised against the
    universe (which uses ``-`` per yfinance convention).
    """
    universe_set = {t.upper() for t in universe}

    candidates = _TICKER_RE.findall(prompt)
    seen: list[str] = []
    for raw in candidates:
        norm = raw.replace(".", "-").upper()
        if norm in universe_set and norm not in seen:
            seen.append(norm)

    hold_intent = bool(_HOLD_RE.search(prompt))

    if len(seen) == 0:
        intent: Intent = "screen"
    elif len(seen) == 1:
        intent = "single"
    else:
        intent = "multi"

    return ParsedPrompt(tickers=tuple(seen), intent=intent, hold_intent=hold_intent)
