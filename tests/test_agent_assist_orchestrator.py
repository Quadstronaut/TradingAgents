"""Orchestrator wiring tests. Every external call (LLM, yfinance, propagate, input) is mocked."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tradingagents.agent_assist.menu import Task
from tradingagents.agent_assist.orchestrator import (
    _extract_rating,
    _run_budget,
    _theme_filter,
    main,
    run_task,
)
from tradingagents.agent_assist.shortlist import PricedCandidate


UNIVERSE_DF = pd.DataFrame([
    {"ticker": "NVDA", "name": "NVIDIA", "sector": "Tech", "industry": "Semis"},
    {"ticker": "AMD", "name": "Advanced Micro Devices", "sector": "Tech", "industry": "Semis"},
    {"ticker": "INTC", "name": "Intel", "sector": "Tech", "industry": "Semis"},
])


@pytest.fixture
def fake_graph():
    """Patches TradingAgentsGraph so propagate() returns canned ratings without LLM calls."""
    fake = MagicMock()
    fake.propagate.return_value = ({}, "**Recommendation**: Buy\nFINAL TRANSACTION PROPOSAL: **BUY**")
    with patch(
        "tradingagents.agent_assist.orchestrator.TradingAgentsGraph",
        return_value=fake,
    ):
        yield fake


@pytest.fixture
def universe_loader():
    with patch(
        "tradingagents.agent_assist.orchestrator.load_universe",
        return_value=UNIVERSE_DF,
    ):
        yield


@pytest.mark.unit
def test_single_ticker_skips_shortlist_and_runs_propagate_once(
    fake_graph, universe_loader, tmp_path
):
    # "buy" doesn't trigger hold_intent → no position prompt; new single
    # path doesn't ask for per-ticker confirm either (menu confirm or
    # caller's confirm is the gate).
    with patch("builtins.input", side_effect=[]):
        rc = main(prompt="should I buy NVDA", budget=None, output_dir=tmp_path)

    assert rc == 0
    assert fake_graph.propagate.call_count == 1
    args, kwargs = fake_graph.propagate.call_args
    assert args[0] == "NVDA"
    assert kwargs.get("additional_portfolio_context", "") == ""


@pytest.mark.unit
def test_screen_intent_triggers_shortlist_then_per_ticker_confirms(
    fake_graph, universe_loader, tmp_path
):
    fake_shortlist = [
        PricedCandidate(ticker="AMD", reasoning="growth", last_price=142.0),
        PricedCandidate(ticker="INTC", reasoning="value", last_price=24.0),
    ]
    inputs = ["100", "y", "s"]  # budget interactive: 100; AMD: y; INTC: skip
    with patch("tradingagents.agent_assist.orchestrator.shortlist", return_value=fake_shortlist), \
         patch("builtins.input", side_effect=inputs):
        rc = main(prompt="what tech to buy", budget=None, output_dir=tmp_path)

    assert rc == 0
    assert fake_graph.propagate.call_count == 1
    args, _ = fake_graph.propagate.call_args
    assert args[0] == "AMD"


@pytest.mark.unit
def test_per_ticker_abort_breaks_loop_immediately(
    fake_graph, universe_loader, tmp_path
):
    fake_shortlist = [
        PricedCandidate(ticker="AMD", reasoning="x", last_price=100.0),
        PricedCandidate(ticker="INTC", reasoning="y", last_price=24.0),
    ]
    inputs = ["", "a"]  # budget skip; AMD: abort
    with patch("tradingagents.agent_assist.orchestrator.shortlist", return_value=fake_shortlist), \
         patch("builtins.input", side_effect=inputs):
        rc = main(prompt="tech anything", budget=None, output_dir=tmp_path)

    assert rc == 0
    assert fake_graph.propagate.call_count == 0


@pytest.mark.unit
def test_position_context_threaded_into_propagate_when_provided(
    fake_graph, universe_loader, tmp_path
):
    inputs = ["50", "130"]  # ask_position: 50 shares, $130 basis
    with patch("builtins.input", side_effect=inputs):
        rc = main(prompt="should I sell NVDA", budget=None, output_dir=tmp_path)

    assert rc == 0
    assert fake_graph.propagate.call_count == 1
    _, kwargs = fake_graph.propagate.call_args
    ctx = kwargs.get("additional_portfolio_context", "")
    assert "50 shares of NVDA" in ctx
    assert "$130" in ctx


@pytest.mark.unit
def test_propagate_exception_marks_failed_and_continues(
    fake_graph, universe_loader, tmp_path
):
    fake_shortlist = [
        PricedCandidate(ticker="AMD", reasoning="x", last_price=100.0),
        PricedCandidate(ticker="INTC", reasoning="y", last_price=24.0),
    ]
    fake_graph.propagate.side_effect = [
        RuntimeError("boom"),
        ({}, "**Recommendation**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**"),
    ]
    inputs = ["", "y", "y"]  # budget skip; AMD: y (will fail); INTC: y
    with patch("tradingagents.agent_assist.orchestrator.shortlist", return_value=fake_shortlist), \
         patch("builtins.input", side_effect=inputs):
        rc = main(prompt="tech anything", budget=None, output_dir=tmp_path)

    assert rc == 0
    assert fake_graph.propagate.call_count == 2
    summaries = list(tmp_path.glob("*.md"))
    assert len(summaries) == 1
    content = summaries[0].read_text(encoding="utf-8")
    assert "FAILED" in content
    assert "boom" in content


@pytest.mark.unit
def test_explicit_budget_arg_skips_interactive_budget_prompt(
    fake_graph, universe_loader, tmp_path
):
    fake_shortlist = [
        PricedCandidate(ticker="AMD", reasoning="x", last_price=24.0),
        PricedCandidate(ticker="INTC", reasoning="y", last_price=24.0),
    ]
    inputs = ["s", "s"]  # both per-ticker prompts: skip. NO budget prompt expected.
    with patch("tradingagents.agent_assist.orchestrator.shortlist", return_value=fake_shortlist) as m, \
         patch("builtins.input", side_effect=inputs):
        rc = main(prompt="what tech to buy", budget=50, output_dir=tmp_path)

    assert rc == 0
    _, kwargs = m.call_args
    assert kwargs.get("budget") == 50


# ---------------------------------------------------------------------------
# _run_budget: pre-filter + theme handling
# ---------------------------------------------------------------------------


BUDGET_UNIVERSE = pd.DataFrame([
    {"ticker": "F",    "name": "Ford",                 "sector": "Consumer Discretionary", "industry": "Automobiles"},
    {"ticker": "BAC",  "name": "Bank of America",      "sector": "Financials",             "industry": "Diversified Banks"},
    {"ticker": "INTC", "name": "Intel",                "sector": "Information Technology", "industry": "Semiconductors"},
    {"ticker": "PFE",  "name": "Pfizer",               "sector": "Health Care",            "industry": "Pharmaceuticals"},
    {"ticker": "ALNY", "name": "Alnylam Pharma",       "sector": "Health Care",            "industry": "Biotechnology"},
    {"ticker": "NVDA", "name": "NVIDIA",               "sector": "Information Technology", "industry": "Semiconductors"},
    {"ticker": "ASML", "name": "ASML Holding",         "sector": "Information Technology", "industry": "Semis Equipment"},
])


@pytest.mark.unit
def test_theme_filter_substring_matches_across_columns():
    out = _theme_filter(BUDGET_UNIVERSE, "biotech")
    assert sorted(out["ticker"].tolist()) == ["ALNY"]

    out = _theme_filter(BUDGET_UNIVERSE, "semi")
    # NVDA/INTC/ASML all have "Semi" in industry, case-insensitively
    assert sorted(out["ticker"].tolist()) == ["ASML", "INTC", "NVDA"]


@pytest.mark.unit
def test_run_budget_filters_universe_to_affordables_before_shortlist(tmp_path):
    """LLM must only see sub-budget rows; shortlist must get precomputed_prices."""
    prices = {
        "F": 11.0, "BAC": 38.0, "INTC": 22.0, "PFE": 28.0,
        "ALNY": 245.0, "NVDA": 920.0, "ASML": 980.0,
    }

    fake_picks = [
        PricedCandidate(ticker="F", reasoning="cheap", last_price=11.0),
        PricedCandidate(ticker="INTC", reasoning="value", last_price=22.0),
    ]

    with patch("tradingagents.agent_assist.orchestrator.bulk_price", return_value=prices), \
         patch("tradingagents.agent_assist.orchestrator.shortlist",
               return_value=fake_picks) as mock_shortlist, \
         patch("tradingagents.agent_assist.orchestrator._run_one_deep") as run_deep, \
         patch("builtins.input", side_effect=["s", "s"]):
        run_deep.return_value = MagicMock(
            ticker="X", rating="SKIPPED", log_path=None, error=None
        )
        rc = _run_budget(
            Task(intent="budget", budget=25),
            output_dir=tmp_path,
            universe_df=BUDGET_UNIVERSE,
        )

    assert rc == 0
    args, kwargs = mock_shortlist.call_args
    sent_universe = args[1]
    sent_tickers = sorted(sent_universe["ticker"].tolist())
    # Only price <= 25 should be passed
    assert sent_tickers == ["F", "INTC"]
    sent_prices = kwargs["precomputed_prices"]
    assert sent_prices == {"F": 11.0, "INTC": 22.0}


@pytest.mark.unit
def test_run_budget_emits_diagnostic_when_too_few_affordables(tmp_path, capsys):
    """All names priced above budget → exit 3 with a specific message, no LLM call."""
    prices = {"BAC": 38.0, "INTC": 22.0, "ALNY": 245.0, "NVDA": 920.0}

    with patch("tradingagents.agent_assist.orchestrator.bulk_price", return_value=prices), \
         patch("tradingagents.agent_assist.orchestrator.shortlist") as mock_shortlist:
        rc = _run_budget(
            Task(intent="budget", budget=10),
            output_dir=tmp_path,
            universe_df=BUDGET_UNIVERSE,
        )

    assert rc == 3
    mock_shortlist.assert_not_called()
    out = capsys.readouterr().out
    assert "trade at or below $10" in out


@pytest.mark.unit
def test_run_budget_theme_hard_filter_used_when_enough_survivors(tmp_path):
    """When >= _THEME_HARD_MIN names match the theme, the LLM should see the hard-filtered set."""
    prices = {
        "F": 11.0, "BAC": 38.0, "INTC": 22.0, "PFE": 28.0,
        "ALNY": 245.0, "NVDA": 920.0, "ASML": 980.0,
    }
    # Bump budget so most rows survive, then verify the theme filter narrows the set
    # being handed to shortlist. With _THEME_HARD_MIN=5 we need a wide theme.
    # Use "Information Technology" which matches INTC, NVDA, ASML — only 3 affordable
    # below $1000 means hard filter has <5 → should soft-fallback to all affordables.
    # To force the hard-filter path we need at least 5 affordable rows matching the theme.
    # Build a synthetic universe with 6 IT rows priced below 1000.
    df = pd.DataFrame([
        {"ticker": f"IT{i}", "name": f"InfoTech{i}", "sector": "Information Technology", "industry": "Software"}
        for i in range(6)
    ] + [
        {"ticker": "OIL1", "name": "Oil Co", "sector": "Energy", "industry": "E&P"},
    ])
    p = {f"IT{i}": 10.0 + i for i in range(6)}
    p["OIL1"] = 12.0

    fake_picks = [
        PricedCandidate(ticker="IT0", reasoning="a", last_price=10.0),
        PricedCandidate(ticker="IT1", reasoning="b", last_price=11.0),
    ]

    with patch("tradingagents.agent_assist.orchestrator.bulk_price", return_value=p), \
         patch("tradingagents.agent_assist.orchestrator.shortlist",
               return_value=fake_picks) as mock_shortlist, \
         patch("tradingagents.agent_assist.orchestrator._run_one_deep"), \
         patch("builtins.input", side_effect=["s", "s"]):
        rc = _run_budget(
            Task(intent="budget", budget=100, theme="Information Technology"),
            output_dir=tmp_path,
            universe_df=df,
        )

    assert rc == 0
    args, _ = mock_shortlist.call_args
    sent_tickers = sorted(args[1]["ticker"].tolist())
    # Hard filter kept the 6 IT rows, dropped OIL1
    assert sent_tickers == [f"IT{i}" for i in range(6)]


@pytest.mark.unit
def test_run_budget_theme_soft_fallback_when_hard_match_too_sparse(tmp_path, capsys):
    """When the theme yields < _THEME_HARD_MIN hard matches, LLM should see all affordables."""
    prices = {
        "F": 11.0, "BAC": 38.0, "INTC": 22.0, "PFE": 28.0,
        "ALNY": 245.0, "NVDA": 920.0, "ASML": 980.0,
    }
    # "science" only matches via name/industry — likely zero hard matches in this universe.
    fake_picks = [
        PricedCandidate(ticker="PFE", reasoning="a", last_price=28.0),
        PricedCandidate(ticker="INTC", reasoning="b", last_price=22.0),
    ]
    with patch("tradingagents.agent_assist.orchestrator.bulk_price", return_value=prices), \
         patch("tradingagents.agent_assist.orchestrator.shortlist",
               return_value=fake_picks) as mock_shortlist, \
         patch("tradingagents.agent_assist.orchestrator._run_one_deep"), \
         patch("builtins.input", side_effect=["s", "s"]):
        rc = _run_budget(
            Task(intent="budget", budget=30, theme="science"),
            output_dir=tmp_path,
            universe_df=BUDGET_UNIVERSE,
        )

    assert rc == 0
    out = capsys.readouterr().out
    assert "soft-match" in out
    args, _ = mock_shortlist.call_args
    sent = sorted(args[1]["ticker"].tolist())
    # Soft fallback: all affordables (price <= 30) passed through regardless of theme.
    # BAC ($38) is above budget so it's not in the affordable set.
    assert sent == ["F", "INTC", "PFE"]


# ---------------------------------------------------------------------------
# Multi-ticker freeform: bulk confirm gates N×15-min deep runs
# ---------------------------------------------------------------------------


# Universe used for multi-ticker freeform tests (includes more tickers so
# multiple matches are possible).
MULTI_UNIVERSE_DF = pd.DataFrame([
    {"ticker": "NVDA", "name": "NVIDIA", "sector": "Tech", "industry": "Semis"},
    {"ticker": "AMD", "name": "Advanced Micro Devices", "sector": "Tech", "industry": "Semis"},
    {"ticker": "INTC", "name": "Intel", "sector": "Tech", "industry": "Semis"},
])


@pytest.fixture
def multi_universe_loader():
    with patch(
        "tradingagents.agent_assist.orchestrator.load_universe",
        return_value=MULTI_UNIVERSE_DF,
    ):
        yield


@pytest.mark.unit
def test_multi_ticker_freeform_runs_all_after_bulk_confirm_y(
    fake_graph, multi_universe_loader, tmp_path,
):
    """User says 'compare AMD vs INTC vs NVDA', accepts the bulk-confirm
    prompt → all three deep runs fire."""
    inputs = ["y"]  # bulk confirm
    with patch("builtins.input", side_effect=inputs):
        rc = main(
            prompt="compare AMD vs INTC vs NVDA",
            budget=None,
            output_dir=tmp_path,
        )

    assert rc == 0
    assert fake_graph.propagate.call_count == 3
    called_tickers = sorted(c.args[0] for c in fake_graph.propagate.call_args_list)
    assert called_tickers == ["AMD", "INTC", "NVDA"]


@pytest.mark.unit
def test_multi_ticker_freeform_aborts_cleanly_on_bulk_confirm_n(
    fake_graph, multi_universe_loader, tmp_path, capsys,
):
    """Declining the bulk confirm must short-circuit — no propagate calls."""
    inputs = ["n"]
    with patch("builtins.input", side_effect=inputs):
        rc = main(
            prompt="compare AMD vs INTC",
            budget=None,
            output_dir=tmp_path,
        )

    assert rc == 0
    fake_graph.propagate.assert_not_called()
    out = capsys.readouterr().out
    assert "Aborted" in out


@pytest.mark.unit
def test_multi_ticker_freeform_default_empty_input_is_no(
    fake_graph, multi_universe_loader, tmp_path,
):
    """Pressing Enter (empty) defaults to no — bulk runs are expensive; the
    safe default is don't kick them off."""
    inputs = [""]  # Enter
    with patch("builtins.input", side_effect=inputs):
        rc = main(
            prompt="compare AMD vs INTC",
            budget=None,
            output_dir=tmp_path,
        )

    assert rc == 0
    fake_graph.propagate.assert_not_called()


@pytest.mark.unit
def test_multi_ticker_freeform_reprompts_on_invalid_then_accepts(
    fake_graph, multi_universe_loader, tmp_path,
):
    """Bad input → reprompt, eventually accept."""
    inputs = ["maybe", "y"]
    with patch("builtins.input", side_effect=inputs):
        rc = main(
            prompt="compare AMD vs INTC",
            budget=None,
            output_dir=tmp_path,
        )

    assert rc == 0
    assert fake_graph.propagate.call_count == 2


# ---------------------------------------------------------------------------
# run_task: normalises ticker / ticker_b at the boundary
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_owned_raises_clear_error_when_shares_missing(
    fake_graph, universe_loader, tmp_path,
):
    """A programmatic Task(intent='owned', ticker='X') without shares or
    cost_basis used to crash with a cryptic TypeError on the :g format.
    Now raises ValueError with the missing fields named."""
    task = Task(intent="owned", ticker="NVDA")  # shares + cost_basis omitted
    with pytest.raises(ValueError, match="shares and cost_basis"):
        run_task(task, output_dir=tmp_path)


@pytest.mark.unit
def test_run_owned_raises_when_only_cost_basis_missing(
    fake_graph, universe_loader, tmp_path,
):
    task = Task(intent="owned", ticker="NVDA", shares=50)
    with pytest.raises(ValueError, match="shares and cost_basis"):
        run_task(task, output_dir=tmp_path)


@pytest.mark.unit
def test_run_task_normalises_specific_ticker_before_propagate(
    fake_graph, universe_loader, tmp_path
):
    """A programmatic Task with a non-canonical ticker must reach propagate
    in canonical form (the menu+freeform paths normalise too — run_task is
    the safety net for direct callers like tests and scripts)."""
    task = Task(intent="specific", ticker="brk.b")
    rc = run_task(task, output_dir=tmp_path)

    assert rc == 0
    args, _ = fake_graph.propagate.call_args
    assert args[0] == "BRK-B"


@pytest.mark.unit
def test_run_task_normalises_owned_ticker_and_position_context(
    fake_graph, universe_loader, tmp_path
):
    """Owned flow builds a position-context string from task.ticker — that
    string must use the canonical form too."""
    task = Task(intent="owned", ticker="brk.b", shares=10, cost_basis=400.0)
    rc = run_task(task, output_dir=tmp_path)

    assert rc == 0
    args, kwargs = fake_graph.propagate.call_args
    assert args[0] == "BRK-B"
    pos_ctx = kwargs.get("additional_portfolio_context", "")
    assert "BRK-B" in pos_ctx
    assert "brk.b" not in pos_ctx and "BRK.B" not in pos_ctx


@pytest.mark.unit
def test_run_task_normalises_both_compare_tickers(
    fake_graph, universe_loader, tmp_path
):
    """Compare flow takes two tickers — both must be normalised."""
    fake_graph.propagate.side_effect = [
        ({}, "FINAL TRANSACTION PROPOSAL: **BUY**"),
        ({}, "FINAL TRANSACTION PROPOSAL: **HOLD**"),
    ]
    # Mock the compare summariser so we don't hit a real LLM
    from unittest.mock import patch as _patch
    with _patch(
        "tradingagents.agent_assist.compare._summarize_comparison",
    ) as _summ:
        from tradingagents.agent_assist.compare import ComparisonVerdict
        _summ.return_value = ComparisonVerdict(winner="A", reasoning="x")
        task = Task(intent="compare", ticker="brk.b", ticker_b="bf.b")
        rc = run_task(task, output_dir=tmp_path)

    assert rc == 0
    # Two propagate calls, both with normalised tickers
    call_tickers = [c.args[0] for c in fake_graph.propagate.call_args_list]
    assert call_tickers == ["BRK-B", "BF-B"]


# ---------------------------------------------------------------------------
# _extract_rating: canonical marker + deterministic fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("marker,expected", [
    ("FINAL TRANSACTION PROPOSAL: **Buy**", "Buy"),
    ("FINAL TRANSACTION PROPOSAL: **OVERWEIGHT**", "Overweight"),
    ("FINAL TRANSACTION PROPOSAL: **hold**", "Hold"),
    ("FINAL TRANSACTION PROPOSAL:  **Underweight** ", "Underweight"),
    ("final transaction proposal: **Sell**", "Sell"),
])
def test_extract_rating_canonical_marker_case_insensitive(marker, expected):
    assert _extract_rating(f"some prose\n{marker}\n") == expected


@pytest.mark.unit
def test_extract_rating_returns_hold_on_empty_or_none():
    assert _extract_rating("") == "Hold"
    assert _extract_rating(None) == "Hold"
    assert _extract_rating("no rating tokens here at all") == "Hold"


@pytest.mark.unit
def test_extract_rating_fallback_picks_last_match_not_first():
    """When the canonical marker is missing, the verdict sits at the end of
    the doc. Mid-doc quotes of other ratings must NOT override the final."""
    md = (
        "**Recommendation**: bull says **Buy** but bear says **Sell**.\n"
        "After weighing both: **Hold** is the right call.\n"
    )
    assert _extract_rating(md) == "Hold"


@pytest.mark.unit
def test_extract_rating_fallback_is_deterministic_across_repeated_calls():
    """The pre-fix code iterated a Python set (hash-randomized order). Run
    the same input many times and confirm identical output every time."""
    md = (
        "Analyst: this is a **Buy** opportunity.\n"
        "Risk debater: counters with **Sell** in volatile regime.\n"
        "Final synthesis: **Overweight** with caveats.\n"
    )
    results = {_extract_rating(md) for _ in range(500)}
    assert results == {"Overweight"}, f"non-deterministic: {results}"


@pytest.mark.unit
def test_extract_rating_canonical_wins_over_mid_doc_tokens():
    """Canonical FINAL TRANSACTION marker beats any other token in the doc."""
    md = (
        "**Buy** in the early thesis section.\n"
        "Reconsidered after risk debate.\n"
        "FINAL TRANSACTION PROPOSAL: **Sell**\n"
        "Trailing notes mention **Hold** once more.\n"
    )
    # Canonical marker captures Sell despite Hold appearing afterwards.
    assert _extract_rating(md) == "Sell"


@pytest.mark.unit
def test_extract_rating_tolerates_whitespace_inside_bold_tokens():
    md = "Final view: **  Overweight  ** with monitoring."
    assert _extract_rating(md) == "Overweight"


# ---------------------------------------------------------------------------
# _run_budget continued
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_budget_falls_back_to_legacy_path_when_bulk_price_returns_empty(tmp_path):
    """yfinance unreachable → no prices → must still try the LLM-only path, not exit 3."""
    fake_picks = [
        PricedCandidate(ticker="INTC", reasoning="a", last_price=22.0),
        PricedCandidate(ticker="F", reasoning="b", last_price=11.0),
    ]
    with patch("tradingagents.agent_assist.orchestrator.bulk_price", return_value={}), \
         patch("tradingagents.agent_assist.orchestrator.shortlist",
               return_value=fake_picks) as mock_shortlist, \
         patch("tradingagents.agent_assist.orchestrator._run_one_deep"), \
         patch("builtins.input", side_effect=["s", "s"]):
        rc = _run_budget(
            Task(intent="budget", budget=25),
            output_dir=tmp_path,
            universe_df=BUDGET_UNIVERSE,
        )

    assert rc == 0
    args, kwargs = mock_shortlist.call_args
    # Legacy path: full universe, no precomputed_prices
    assert "precomputed_prices" not in kwargs or kwargs.get("precomputed_prices") is None
    assert sorted(args[1]["ticker"].tolist()) == sorted(BUDGET_UNIVERSE["ticker"].tolist())
