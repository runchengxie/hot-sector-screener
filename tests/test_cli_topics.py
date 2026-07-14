from __future__ import annotations

import argparse
import json
from unittest.mock import Mock

import pytest

from hot_sector_screener import cli
from hot_sector_screener.topic_classifier import TopicValidationError


def test_run_cli_fails_closed_when_loaded_topics_are_rejected(tmp_path, monkeypatch, capsys):
    topics_path = tmp_path / "topics.json"
    topics_path.write_text(
        json.dumps(
            [
                {
                    "topic": "AI精选",
                    "weight": 1.0,
                    "reasoning": "直接选股",
                    "related_concepts": ["300308.SZ"],
                    "source_signals": ["model_pick"],
                }
            ]
        ),
        encoding="utf-8",
    )
    builder = Mock()
    builder.build_universe.side_effect = TopicValidationError("stock selection is forbidden")
    monkeypatch.setattr(cli, "_resolve_config", Mock(return_value={}))
    monkeypatch.setattr(cli, "Screener", Mock(return_value=builder))
    args = argparse.Namespace(
        config=None,
        no_llm=False,
        max_candidates=None,
        stocks_per_topic=None,
        load_topics=str(topics_path),
        date="2026-06-19",
        output_dir=None,
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.cmd_run(args)

    assert exc_info.value.code == 1
    assert "ERROR: invalid topics" in capsys.readouterr().err
