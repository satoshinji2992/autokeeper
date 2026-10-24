"""Evaluation limits and ledgers cannot become a real table's spending limit."""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autokeeper import config, paths
from autokeeper.cli import app
from autokeeper.harness import spend
from autokeeper.harness.spend import BudgetExceeded

MODEL = "deepseek:deepseek-flash"


@pytest.fixture(autouse=True)
def unrestricted_game(monkeypatch):
    monkeypatch.delenv("AUTOKEEPER_BUDGET_USD", raising=False)


def test_an_exhausted_evaluation_does_not_stop_unlimited_real_play(tmp_path):
    spend.record(MODEL, {}, cost=5)
    original = paths.spend().read_bytes()
    evaluation = tmp_path / "evaluation.json"
    assert spend.budget() is None
    with spend.scoped(10, evaluation):
        spend.guard(MODEL)
        spend.record(MODEL, {}, cost=10)
        with pytest.raises(BudgetExceeded):
            spend.guard(MODEL)
        assert spend.budget() == 10 and spend.ledger()["total_usd"] == 10
    assert spend.budget() is None
    spend.guard(MODEL)
    assert paths.spend().read_bytes() == original
    assert json.loads(evaluation.read_text())["total_usd"] == 10


def test_an_explicit_global_game_limit_still_applies_after_an_evaluation(tmp_path):
    settings = paths.USER_DIR / "config.toml"
    settings.parent.mkdir(parents=True)
    settings.write_text("budget_usd = 2\n")
    spend.record(MODEL, {}, cost=3)
    with spend.scoped(10, tmp_path / "evaluation.json"):
        spend.guard(MODEL)
    with pytest.raises(BudgetExceeded):
        spend.guard(MODEL)


def test_changing_a_mistaken_limit_unblocks_an_existing_process_without_erasing_its_ledger():
    settings = paths.USER_DIR / "config.toml"
    settings.parent.mkdir(parents=True)
    settings.write_text("budget_usd = 10\n")
    spend.record(MODEL, {}, cost=11)
    before = paths.spend().read_bytes()
    with pytest.raises(BudgetExceeded):
        spend.guard(MODEL)
    settings.write_text("preset = 'deepseek'\n")
    assert config.load().budget_usd is None and spend.budget() is None
    spend.guard(MODEL)
    assert paths.spend().read_bytes() == before


def _evaluation_worker(args: tuple[str, float]) -> bool:
    ledger, cost = args
    with spend.scoped(10, Path(ledger)):
        if cost:
            spend.record(MODEL, {}, cost=cost)
        try:
            spend.guard(MODEL)
        except BudgetExceeded:
            return True
        return False


def test_processes_joining_one_evaluation_share_its_ledger_and_hard_limit(tmp_path):
    ledger = tmp_path / "shared-evaluation.json"
    with ProcessPoolExecutor(2) as pool:
        list(pool.map(_evaluation_worker, [(str(ledger), 5)] * 2))
        assert pool.submit(_evaluation_worker, (str(ledger), 0)).result()
    assert json.loads(ledger.read_text())["total_usd"] == 10
    assert not paths.spend().exists()
    spend.guard(MODEL)


@pytest.mark.parametrize("command,module", [(["eval", "--budget", "10", "rhetoric"], "evaluation"),
                                           (["autoplay", "unused-module", "--budget", "10"], "autoplay")])
def test_cli_evaluation_budget_is_local_even_when_the_run_raises(monkeypatch, command, module):
    from autokeeper.engine import autoplay
    from autokeeper.eval import rhetoric

    async def bounded(*args, **kwargs):
        assert spend.budget() == 10
        spend.record(MODEL, {}, cost=10)
        spend.guard(MODEL)

    monkeypatch.setattr(rhetoric if module == "evaluation" else autoplay, "run", bounded)
    monkeypatch.setattr(paths, "prepare", lambda: None)
    monkeypatch.setattr(paths, "adopt_known", lambda: {})
    result = CliRunner().invoke(app, command)
    assert isinstance(result.exception, BudgetExceeded)
    assert spend.budget() is None and not paths.spend().exists()
    spend.guard(MODEL)
    ledgers = list(paths.dev_output("spend").glob("*.json"))
    assert len(ledgers) == 1 and json.loads(ledgers[0].read_text())["total_usd"] == 10
