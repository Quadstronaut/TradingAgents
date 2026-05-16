"""Harness self-tests for the vague-prompt picker."""

from __future__ import annotations

import pytest

from tests.verify.fixtures import vague_prompts as vp


@pytest.mark.unit
class TestPickPrompt:
    def test_returns_string(self):
        assert isinstance(vp.pick_prompt(0), str)

    def test_three_passes_yield_three_distinct_prompts(self):
        chosen = {vp.pick_prompt(i) for i in range(3)}
        assert len(chosen) == 3

    def test_full_rotor_covers_pool(self):
        n = vp.pool_size()
        seen = {vp.pick_prompt(i) for i in range(n)}
        assert len(seen) == n

    def test_negative_pass_no_rejected(self):
        with pytest.raises(ValueError):
            vp.pick_prompt(-1)


@pytest.mark.unit
class TestAllPrompts:
    def test_pool_size_matches_all(self):
        assert vp.pool_size() == len(vp.all_prompts())

    def test_no_duplicates_in_pool(self):
        prompts = vp.all_prompts()
        assert len(set(prompts)) == len(prompts)

    def test_pool_is_non_trivial(self):
        # At least 10 entries so a 3-pass run has real variety.
        assert vp.pool_size() >= 10
