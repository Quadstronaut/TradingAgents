"""Seeded picker over a frozen pool of \"vague non-owned\" user prompts.

These are the 'I don't own this, just curious' prompts the freeform /
theme / budget intents are exercised against. Each prompt is hand-tuned
to hit a different shortlist axis (sector, theme, defensive, growth,
small-cap, income).
"""

from __future__ import annotations

# Frozen, deterministically ordered. Adding to the pool is fine; removing
# or reordering changes the rotor's output for prior pass numbers — only
# do that with a comment explaining the rationale and a one-shot pass to
# regenerate any committed verification logs.
_PROMPTS: tuple[str, ...] = (
    "best AI plays this week",
    "cheap biotech under $50 a share",
    "boring dividend stocks for an income sleeve",
    "renewable energy plays with rising margins",
    "semis with positive cash flow this quarter",
    "defensive consumer staples in a risk-off tape",
    "undervalued small-cap industrials",
    "new IPOs gaining real traction this month",
    "momentum names with healthy short interest",
    "value picks under $30 with a wide moat",
)

# Co-prime with pool size (10) to fully rotate before repeating. Using 3
# means passes 0,1,2 sample three well-separated prompts.
_DEFAULT_STEP = 3


def pool_size() -> int:
    return len(_PROMPTS)


def pick_prompt(pass_no: int, *, step: int = _DEFAULT_STEP) -> str:
    """Return a deterministic vague prompt for ``pass_no``."""
    if pass_no < 0:
        raise ValueError(f"pass_no must be non-negative; got {pass_no!r}")
    return _PROMPTS[(pass_no * step) % len(_PROMPTS)]


def all_prompts() -> tuple[str, ...]:
    """Return the full pool (for harness tests / coverage analysis)."""
    return _PROMPTS
