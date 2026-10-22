"""Where things go on disk: a checkout, an installed package, and the saves carried between them (paths.py)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autokeeper import paths
from autokeeper.cli import app


def checkout(root: Path) -> Path:
    """A folder laid out like the repository, with a save, a card and an imported module."""
    (root / "src" / "autokeeper").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (root / "modules" / "local" / "mine").mkdir(parents=True)
    (root / "modules" / "local" / "mine" / "module.yaml").write_text("id: mine\n", encoding="utf-8")
    games = root / "data" / "games"
    games.mkdir(parents=True)
    (games / "old.json").write_text("{}", encoding="utf-8")
    (games / "index.json").write_text(json.dumps({"old": {"label": "旧团"}}), encoding="utf-8")
    (games / "rooms").mkdir()
    (games / "rooms" / "8765.key").write_text("k", encoding="utf-8")
    (root / "data" / "cards").mkdir()
    (root / "data" / "cards" / "chen.json").write_text("{}", encoding="utf-8")
    (root / "data" / "cache" / "models" / "bge").mkdir(parents=True)
    (root / "data" / "cache" / "models" / "bge" / "w.bin").write_text("x", encoding="utf-8")
    (root / "data" / "cache" / "sessions").mkdir()
    (root / "data" / "logs").mkdir()
    (root / "data" / "logs" / "web.log").write_text("log", encoding="utf-8")
    (root / "data" / "preferences.json").write_text("{}", encoding="utf-8")
    return root


@pytest.fixture
def installed(monkeypatch, tmp_path):
    """This process as an installed package: the user's own folders, the package's bundle."""
    data, cache, bundle = tmp_path / "share" / "autokeeper", tmp_path / "cache" / "autokeeper", tmp_path / "bundle"
    folders = paths.layout(None, True, data, cache)._replace(root=bundle, modules=bundle / "modules", exports=tmp_path / "docs")
    for name, value in folders._asdict().items():
        monkeypatch.setattr(paths, name.upper(), value)
    monkeypatch.setattr(paths, "INSTALLED", True)
    monkeypatch.setattr(paths, "BUNDLE", bundle)
    monkeypatch.setattr(paths, "DATA_HOME", data)
    (bundle / "bridge").mkdir(parents=True)
    for name in ("bcdice_bridge.mjs", "package.json", "package-lock.json"):
        (bundle / "bridge" / name).write_text(name, encoding="utf-8")
    return folders


def test_the_repository_is_a_checkout_and_keeps_everything_inside():
    assert paths.is_checkout(paths.SOURCE) and not paths.INSTALLED
    folders = paths.layout(None, False, Path("/nowhere"), Path("/nowhere"))
    assert folders.root == paths.SOURCE and folders.data == paths.SOURCE / "data"
    assert folders.bridge == paths.SOURCE / "bridge" and folders.local_modules == paths.SOURCE / "modules" / "local"
    assert folders.cache is None and folders.exports is None  # data/cache and data/角色卡导出, as before


def test_autokeeper_home_is_laid_out_like_the_repository(tmp_path):
    folders = paths.layout(str(tmp_path), False, Path("/nowhere"), Path("/nowhere"))
    assert folders.root == tmp_path.resolve() and folders.data == tmp_path.resolve() / "data"
    assert folders.skills == tmp_path.resolve() / "skills"


def test_an_installed_copy_writes_only_to_the_users_folders(tmp_path):
    data, cache = tmp_path / "share", tmp_path / "cache"
    folders = paths.layout(None, True, data, cache)
    assert folders.modules == paths.BUNDLE / "modules"  # read from the package
    writes = [folders.data, folders.local_modules, folders.inbox, folders.skills, folders.bridge, folders.dev_output]
    assert all(p.is_relative_to(data) for p in writes) and folders.cache == cache


@pytest.mark.skipif(os.name == "nt", reason="XDG folders are for Linux and macOS")
def test_the_user_folders_follow_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "k"))
    assert paths._user_dirs() == (tmp_path / "c" / "autokeeper", tmp_path / "d" / "autokeeper", tmp_path / "k" / "autokeeper")


def test_the_cache_and_exports_follow_their_own_folders_when_set(monkeypatch, tmp_path):
    assert paths.cache() == paths.DATA / "cache" and paths.card_exports() == paths.DATA / "角色卡导出"
    monkeypatch.setattr(paths, "CACHE", tmp_path / "k")
    monkeypatch.setattr(paths, "EXPORTS", tmp_path / "e")
    assert paths.models() == tmp_path / "k" / "models" and paths.card_exports() == tmp_path / "e"


def test_modules_are_found_by_id_or_folder():
    assert paths.find_module("zhuishuren") == paths.MODULES / "zhuishuren"
    assert paths.find_module(paths.MODULES / "zhuishuren") == paths.MODULES / "zhuishuren"
    assert paths.find_module("example") == paths.EXAMPLE
    assert paths.find_module("nosuch") is None


def test_shared_skills_come_from_the_package_when_the_skills_folder_lacks_them(installed):
    (paths.BUNDLE / "skills" / "coc7-core").mkdir(parents=True)
    (paths.BUNDLE / "skills" / "coc7-core" / "SKILL.md").write_text("---\nname: coc7-core\n---\n规则", encoding="utf-8")
    (paths.SKILLS / "module-x").mkdir(parents=True)
    (paths.SKILLS / "module-x" / "SKILL.md").write_text("---\nname: module-x\n---\n", encoding="utf-8")
    assert paths.skill("coc7-core") == paths.BUNDLE / "skills" / "coc7-core"
    assert paths.skill("module-x") == paths.SKILLS / "module-x"
    assert [p.name for p in paths.skill_folders()] == ["coc7-core", "module-x"]


def test_the_bridge_is_copied_next_to_its_packages_and_kept_up_to_date(installed):
    paths.prepare()
    assert (paths.BRIDGE / "bcdice_bridge.mjs").read_text(encoding="utf-8") == "bcdice_bridge.mjs"
    (paths.BRIDGE / "node_modules" / "bcdice").mkdir(parents=True)
    (paths.BUNDLE / "bridge" / "bcdice_bridge.mjs").write_text("new", encoding="utf-8")  # a new version of the package
    paths.prepare()
    assert (paths.BRIDGE / "bcdice_bridge.mjs").read_text(encoding="utf-8") == "new"
    assert (paths.BRIDGE / "node_modules" / "bcdice").is_dir()  # the same packages: kept
    (paths.BUNDLE / "bridge" / "package-lock.json").write_text("other packages", encoding="utf-8")
    paths.prepare()
    assert not (paths.BRIDGE / "node_modules").exists()  # installed again on the next start


def test_a_bridge_folder_the_package_did_not_fill_is_left_alone(installed):
    paths.BRIDGE.mkdir(parents=True)
    (paths.BRIDGE / "bcdice_bridge.mjs").write_text("mine", encoding="utf-8")
    paths.prepare()
    assert (paths.BRIDGE / "bcdice_bridge.mjs").read_text(encoding="utf-8") == "mine"


def test_adopting_a_checkout_copies_its_saves_and_never_overwrites(installed, tmp_path):
    old = checkout(tmp_path / "repo")
    paths.games().mkdir(parents=True)
    paths.save("old").write_text('{"newer": true}', encoding="utf-8")
    paths.save_index().write_text(json.dumps({"mine": {"label": "新团"}}), encoding="utf-8")
    counts = paths.adopt(old)
    assert set(counts) >= {"存档", "角色卡", "导入的模组", "本地模型与知识库", "偏好与花费记录"}
    assert paths.save("old").read_text(encoding="utf-8") == '{"newer": true}'  # kept
    assert set(json.loads(paths.save_index().read_text(encoding="utf-8"))) == {"old", "mine"}  # the indexes joined
    assert (paths.cards() / "chen.json").is_file() and (paths.LOCAL_MODULES / "mine" / "module.yaml").is_file()
    assert (paths.models() / "bge" / "w.bin").is_file() and paths.preferences().is_file()
    assert not (paths.games() / "rooms").exists() and not paths.logs().exists() and not paths.sessions().exists()
    assert (old / "data" / "games" / "old.json").is_file()  # the checkout keeps its own
    assert paths.adopt(old) == {} and paths.adopt(old / "data") == {}  # nothing new the second time


def test_a_folder_without_saves_is_not_adopted(installed, tmp_path):
    (tmp_path / "project" / "data" / "cache").mkdir(parents=True)
    assert paths.adopt(tmp_path / "project") == {}
    assert paths.adopt(paths.DATA) == {}


def test_the_first_start_of_an_installed_copy_finds_the_checkouts_played_before(installed, tmp_path, monkeypatch):
    old = checkout(tmp_path / "repo")
    monkeypatch.setattr(paths, "INSTALLED", False)
    monkeypatch.setattr(paths, "ROOT", old)
    paths.remember_checkout()  # writing a save from the checkout does this
    monkeypatch.setattr(paths, "INSTALLED", True)
    monkeypatch.chdir(tmp_path)
    found = paths.adopt_known()
    assert list(found) == [old.resolve()] and paths.save("old").is_file()
    paths.save("old").unlink()  # deleted in the new copy: stays deleted
    assert paths.adopt_known() == {} and not paths.save("old").exists()


def test_migrate_copies_a_named_checkout(installed, tmp_path):
    old = checkout(tmp_path / "repo")
    out = CliRunner().invoke(app, ["migrate", str(old)])
    assert out.exit_code == 0, out.output
    assert "存档" in out.output and paths.save("old").is_file()
    out = CliRunner().invoke(app, ["migrate", str(old)])
    assert "没有要复制的新东西" in out.output.replace("\n", "")


def test_migrate_without_a_folder_says_how(installed, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = CliRunner().invoke(app, ["migrate"])
    assert out.exit_code == 1 and "autokeeper migrate" in out.output.replace("\n", "")


def test_check_takes_a_module_id():
    out = CliRunner().invoke(app, ["check", "example"])
    assert out.exit_code == 0, out.output
    out = CliRunner().invoke(app, ["check", "nosuch"])
    assert out.exit_code == 1 and "找不到模组" in out.output.replace("\n", "")


def test_upgrade_and_showcase_find_modules_as_check_does(monkeypatch):
    """They looked in the modules folder only, so `upgrade example` missed the format sample."""
    from autokeeper.engine import showcase

    async def no_pictures(*args, **kwargs):
        raise AssertionError("the example has no big scenes to prepare")

    monkeypatch.setattr(showcase, "prepare_module", no_pictures)
    out = CliRunner().invoke(app, ["showcase", "example"])
    assert out.exit_code == 0 and "没有大场景" in out.output.replace("\n", ""), out.output
    out = CliRunner().invoke(app, ["upgrade", "example", "--dry-run", "--only", "endings"])
    assert out.exit_code == 0 and "额外结局：要补" in out.output.replace("\n", ""), out.output
    for command in ("showcase", "upgrade"):
        out = CliRunner().invoke(app, [command, "nosuch"])
        assert out.exit_code == 1 and "找不到模组：nosuch" in out.output.replace("\n", ""), command
