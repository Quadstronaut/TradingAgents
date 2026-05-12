"""Tests for the Reddit sentiment JSONL seam.

The Reddit scraper is a separate, future project; these tests pin the
read-side contract so the scraper has a stable target.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tradingagents.agent_assist import reddit_sentiment as rs


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _record(ts_iso: str, **overrides) -> dict:
    base = {
        "ts": ts_iso,
        "sub": "wallstreetbets",
        "score": 0.5,
        "confidence": 0.8,
        "n_posts": 20,
    }
    base.update(overrides)
    return base


@pytest.mark.unit
def test_missing_file_returns_empty_list(tmp_path):
    out = rs.read_records("NVDA", cache_dir=tmp_path)
    assert out == []


@pytest.mark.unit
def test_missing_file_summary_is_user_friendly(tmp_path):
    out = rs.summarize(rs.read_records("NVDA", cache_dir=tmp_path))
    assert "No Reddit sentiment cache" in out


@pytest.mark.unit
def test_reads_valid_records_within_lookback(tmp_path):
    now = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    old = (now - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    _write_jsonl(tmp_path / "NVDA.jsonl", [
        _record(recent, score=0.9, n_posts=50),
        _record(old, score=-0.3, n_posts=10),  # outside window
    ])

    out = rs.read_records("NVDA", lookback_days=7, cache_dir=tmp_path, now=now)
    assert len(out) == 1
    assert out[0].score == 0.9


@pytest.mark.unit
def test_malformed_lines_are_skipped_not_fatal(tmp_path):
    now = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
    valid_ts = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    path = tmp_path / "NVDA.jsonl"
    path.write_text(
        "not even json\n"
        + json.dumps({"ts": "broken"}) + "\n"  # missing required fields
        + json.dumps(_record(valid_ts, score=0.4)) + "\n",
        encoding="utf-8",
    )

    out = rs.read_records("NVDA", cache_dir=tmp_path, now=now)
    assert len(out) == 1
    assert out[0].score == 0.4


@pytest.mark.unit
def test_summary_includes_subreddit_themes_and_quotes(tmp_path):
    now = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
    ts = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    _write_jsonl(tmp_path / "NVDA.jsonl", [
        _record(ts, sub="wallstreetbets", score=0.8, theme="AI demand strong",
                sample_quote="Blackwell ramp looking real"),
        _record(ts, sub="investing", score=0.3, theme="valuation rich"),
    ])

    out = rs.summarize(rs.read_records("NVDA", cache_dir=tmp_path, now=now))
    assert "r/wallstreetbets" in out
    assert "r/investing" in out
    assert "AI demand strong" in out
    assert "Blackwell ramp" in out


@pytest.mark.unit
def test_aggregate_score_is_confidence_and_size_weighted(tmp_path):
    now = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
    ts = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    # High-confidence + large-volume bullish swamps low-confidence bearish.
    _write_jsonl(tmp_path / "NVDA.jsonl", [
        _record(ts, sub="r1", score=1.0, confidence=0.9, n_posts=100),
        _record(ts, sub="r2", score=-1.0, confidence=0.2, n_posts=5),
    ])
    out = rs.summarize(rs.read_records("NVDA", cache_dir=tmp_path, now=now))
    assert "bullish" in out.lower()


@pytest.mark.unit
def test_path_traversal_in_ticker_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        rs.read_records("../etc/passwd", cache_dir=tmp_path)


# ---------------------------------------------------------------------------
# Time-decayed sentiment aggregation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_recent_record_dominates_old_record_of_opposite_sign(tmp_path):
    """A bullish record from today should outweigh an equally-sized bearish
    record from two weeks ago — the practitioner-standard exponential decay
    (α=0.10/day) has the older record at ~14% relative weight."""
    now = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    stale = (now - timedelta(days=14)).isoformat().replace("+00:00", "Z")
    _write_jsonl(tmp_path / "NVDA.jsonl", [
        _record(fresh, sub="r1", score=0.9, confidence=0.8, n_posts=40),
        _record(stale, sub="r2", score=-0.9, confidence=0.8, n_posts=40),
    ])

    records = rs.read_records(
        "NVDA", lookback_days=30, cache_dir=tmp_path, now=now,
    )
    assert len(records) == 2
    out = rs.summarize(records, now=now)
    # Fresh record (positive) wins despite equal raw weights pre-decay.
    assert "bullish" in out.lower()


@pytest.mark.unit
def test_decay_inverts_lean_when_old_signal_was_strong(tmp_path):
    """The opposite case proves the decay actually works: same scores but
    bullish was 14 days ago, bearish is today → aggregate must lean bearish."""
    now = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    stale = (now - timedelta(days=14)).isoformat().replace("+00:00", "Z")
    _write_jsonl(tmp_path / "NVDA.jsonl", [
        _record(stale, sub="r1", score=0.9, confidence=0.8, n_posts=40),
        _record(fresh, sub="r2", score=-0.9, confidence=0.8, n_posts=40),
    ])

    records = rs.read_records(
        "NVDA", lookback_days=30, cache_dir=tmp_path, now=now,
    )
    out = rs.summarize(records, now=now)
    assert "bearish" in out.lower()


@pytest.mark.unit
def test_summary_advertises_time_decay():
    """Tail consumers (an LLM analyst) should be able to read the summary
    and know weights are time-aware. Surface the fact in the header line."""
    now = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
    rec = rs.SentimentRecord(
        ts=now - timedelta(hours=2), sub="wsb",
        score=0.5, confidence=0.7, n_posts=20,
    )
    out = rs.summarize([rec], now=now)
    assert "time-decayed" in out.lower()


@pytest.mark.unit
def test_empty_total_weight_does_not_crash(tmp_path):
    """Edge case: a very old record with α decay can still contribute
    measurable weight, but a contrived all-zero-confidence list yields
    total_weight=0 → divisor protection must return neutral."""
    rec = rs.SentimentRecord(
        ts=datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc),
        sub="wsb",
        score=0.9, confidence=0.0, n_posts=20,
    )
    out = rs.summarize(
        [rec], now=datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc),
    )
    # confidence=0 → weight 0 → aggregate guards against div-by-0 → neutral
    assert "neutral" in out.lower() or "mixed" in out.lower()
