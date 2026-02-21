"""Tests for the bottleneck focus section in the daily Slack report."""

from datetime import date
from unittest.mock import patch

import pytest

from app.services.slack import _get_current_focus, build_daily_report_blocks


SAMPLE_STRATEGY = """\
# Growth Strategy & Prioritization

## Current Funnel (2026-02-21)

```
1,723 Prospects
  270 Conversations
```

## This Week's Focus

1. **Fix AI draft quality** — in progress
2. **Work the backlog** — 93 pending drafts
3. **Reply fast** — speed to lead
4. **Content 5 posts/week**

## Levers to Push Later (ordered by priority)

Some other content here.
"""


class TestGetCurrentFocus:
    """Tests for _get_current_focus()."""

    def test_parses_focus_section(self, tmp_path):
        """Extracts the This Week's Focus section from strategy file."""
        strategy_file = tmp_path / "strategy.md"
        strategy_file.write_text(SAMPLE_STRATEGY, encoding="utf-8")

        result = _get_current_focus(str(strategy_file))

        assert result is not None
        assert "Fix AI draft quality" in result
        assert "Work the backlog" in result
        assert "Reply fast" in result
        assert "Content 5 posts/week" in result

    def test_excludes_content_outside_focus(self, tmp_path):
        """Does not include text from other sections."""
        strategy_file = tmp_path / "strategy.md"
        strategy_file.write_text(SAMPLE_STRATEGY, encoding="utf-8")

        result = _get_current_focus(str(strategy_file))

        assert result is not None
        assert "Levers to Push" not in result
        assert "Current Funnel" not in result

    def test_returns_none_when_file_missing(self):
        """Returns None when the strategy file doesn't exist."""
        result = _get_current_focus("/nonexistent/path/strategy.md")
        assert result is None

    def test_returns_none_when_no_focus_section(self, tmp_path):
        """Returns None when file exists but has no focus section."""
        strategy_file = tmp_path / "strategy.md"
        strategy_file.write_text("# Some other doc\n\nNo focus here.\n")

        result = _get_current_focus(str(strategy_file))
        assert result is None

    def test_handles_focus_at_end_of_file(self, tmp_path):
        """Parses focus section when it's the last section in the file."""
        content = "# Strategy\n\n## This Week's Focus\n\n1. Do the thing\n2. Do another thing\n"
        strategy_file = tmp_path / "strategy.md"
        strategy_file.write_text(content)

        result = _get_current_focus(str(strategy_file))

        assert result is not None
        assert "Do the thing" in result
        assert "Do another thing" in result


class TestDailyReportFocusBlock:
    """Tests for focus block integration in build_daily_report_blocks()."""

    MINIMAL_METRICS = {
        "outreach": {"profiles_scraped": 0, "icp_qualified": 0, "heyreach_uploaded": 0, "costs": {"apify": 0, "deepseek": 0}},
        "conversations": {"new": 0, "drafts_approved": 0, "classifications": {"positive": 0}},
        "funnel": {"pitched": 0, "calendar_sent": 0, "booked": 0},
        "content": {"drafts_created": 0, "drafts_scheduled": 0, "drafts_posted": 0},
        "speed_metrics": {"speed_to_lead": None, "speed_to_reply": None},
    }

    def test_focus_block_appears_when_focus_exists(self, tmp_path):
        """Focus section appears after header/divider when strategy file has focus."""
        strategy_file = tmp_path / "strategy.md"
        strategy_file.write_text(SAMPLE_STRATEGY, encoding="utf-8")

        with patch("app.services.slack._STRATEGY_FILE_PATH", str(strategy_file)):
            blocks = build_daily_report_blocks(date(2026, 2, 21), self.MINIMAL_METRICS)

        # Find the focus block — should be a section with the target emoji
        focus_blocks = [
            b for b in blocks
            if b.get("type") == "section"
            and "This Week's Focus" in str(b.get("text", {}).get("text", ""))
        ]
        assert len(focus_blocks) == 1
        assert "Fix AI draft quality" in focus_blocks[0]["text"]["text"]

    def test_report_still_works_without_focus(self):
        """Report builds fine when strategy file is missing (no focus block)."""
        with patch("app.services.slack._STRATEGY_FILE_PATH", "/nonexistent/strategy.md"):
            blocks = build_daily_report_blocks(date(2026, 2, 21), self.MINIMAL_METRICS)

        # Should still have the header and other blocks
        assert blocks[0]["type"] == "header"
        # No focus block
        focus_blocks = [
            b for b in blocks
            if b.get("type") == "section"
            and "This Week's Focus" in str(b.get("text", {}).get("text", ""))
        ]
        assert len(focus_blocks) == 0

    def test_focus_block_position(self, tmp_path):
        """Focus block appears after header and divider, before metrics."""
        strategy_file = tmp_path / "strategy.md"
        strategy_file.write_text(SAMPLE_STRATEGY, encoding="utf-8")

        with patch("app.services.slack._STRATEGY_FILE_PATH", str(strategy_file)):
            blocks = build_daily_report_blocks(date(2026, 2, 21), self.MINIMAL_METRICS)

        # blocks[0] = header, blocks[1] = divider, blocks[2] = focus section
        assert blocks[0]["type"] == "header"
        assert blocks[1]["type"] == "divider"
        assert blocks[2]["type"] == "section"
        assert "This Week's Focus" in blocks[2]["text"]["text"]
