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
