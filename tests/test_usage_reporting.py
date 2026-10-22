"""Report cached input separately without treating output as fresh input."""

from __future__ import annotations

import io

from rich.console import Console

from autokeeper.engine.game import Game
from autokeeper.harness import Reply, spend, trace
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.tui.modals.usage import usage_view


def test_game_usage_reports_total_and_uncached_input_separately(monkeypatch):
    module = Module.load(EXAMPLE)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.record_usage("keeper", Reply(usage={"input_tokens": 12000, "cache_read": 9000, "output_tokens": 800}))
    state.record_usage("arbiter", Reply(usage={"input_tokens": 10000, "cache_read": 5000, "output_tokens": 200}))
    state.turn = 1
    state.tally()
    monkeypatch.setattr(spend, "ledger", lambda: {"total_usd": 0.0})
    monkeypatch.setattr(spend, "budget", lambda: None)
    console = Console(file=io.StringIO(), width=160, record=True)
    console.print(usage_view(state))
    shown = console.export_text()
    assert "未命中缓存 8.0k token，缓存读取 14.0k token" in shown
    assert "平均总量 23.0k token" in shown and "未命中缓存输入 8.0k token" in shown


def test_trace_old_rows_without_cache_remain_readable_and_bad_rows_do_not_cancel_fresh_input():
    rows = [
        {"agent": "keeper", "input_tokens": 1000},
        {"agent": "keeper", "input_tokens": 2000, "cache_read": 1500, "output_tokens": 300},
        {"agent": "keeper", "input_tokens": 0, "cache_read": 10},
    ]
    summary = trace.summarize(rows)[0]
    assert summary["input"] == 3000 and summary["cached"] == 1510
    assert summary["fresh"] == 1500 and summary["output"] == 300
