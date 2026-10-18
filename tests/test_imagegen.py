"""Pictures from Codex's image tool, with the codex process stood in for: the command line, the
picture found from its output, the budget, the feature off without Codex, the story changing a
place or a person, the save keeping it, and LAN players seeing it."""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from PIL import Image
from rich.console import Console

from autokeeper import config, imagegen, imagery
from autokeeper.agents.cast import Action, Ruling
from autokeeper.agents.director import Improv, NewLook
from autokeeper.config import EXAMPLE
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module import graph, improv
from autokeeper.module.importer import find_photos
from autokeeper.module.schema import Look, Module
from autokeeper.net import media
from autokeeper.net.hub import Hub, table
from autokeeper.rules.coc import Rules
from autokeeper.tui.net import table_module

from .test_engine import FakeCast, Improvising
from .test_net import TableCast

REAL_STATUS = imagegen.status  # the conftest switches it off for every test
RED, BLUE = (200, 40, 30), (30, 60, 200)


class FakeCodex:
    """Stands in for the codex CLI: `exec` saves a picture where Codex keeps them and prints its
    JSON lines (the reply names the file, as our instructions ask); `delete` forgets the session."""

    def __init__(self, home: Path, real):
        self.home, self.real = home, real
        self.colour, self.fail, self.name_it = RED, "", True
        self.calls: list[list[str]] = []
        self.prompts: list[str] = []
        self.attached: list[tuple[int, int]] = []

    async def __call__(self, *args, **kwargs):
        self.calls.append([str(a) for a in args])
        return FakeProcess(self, [str(a) for a in args])


class FakeProcess:
    def __init__(self, codex: FakeCodex, args: list[str]):
        self.codex, self.args, self.returncode, self.pid = codex, args, None, 4242

    async def communicate(self, data: bytes | None = None) -> tuple[bytes, bytes]:
        await asyncio.sleep(0)  # a real process takes a while: let others run meanwhile
        codex, args = self.codex, self.args
        self.returncode = 0
        if args[1] != "exec":
            return b"", b""
        codex.prompts.append((data or b"").decode("utf-8"))
        if "-i" in args:
            with Image.open(args[args.index("-i") + 1]) as im:
                codex.attached.append(im.size)
        thread = f"01a15006-{len(codex.prompts):04d}-7401-ad01-cf3d77c0aaf4"
        lines: list[dict] = [{"type": "thread.started", "thread_id": thread}, {"type": "turn.started"}]
        if codex.fail:
            lines.append({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": f"failed: {codex.fail}"}})
        else:
            folder = codex.home / "generated_images" / thread
            folder.mkdir(parents=True)
            picture = folder / "exec-cb3acaf4.png"
            Image.new("RGB", (1716, 916), codex.colour).save(picture)
            said = str(picture) if codex.name_it else "done"
            lines.append({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": said}})
        lines.append({"type": "turn.completed", "usage": {"input_tokens": 7588, "cached_input_tokens": 2560, "output_tokens": 248}})
        return "\n".join(json.dumps(x) for x in lines).encode(), b""

    async def wait(self) -> int:
        self.returncode = 0
        return 0


@pytest.fixture
def codex(monkeypatch, tmp_path) -> FakeCodex:
    """Pictures switched on, the codex process stood in for; saves and CODEX_HOME in tmp_path."""
    monkeypatch.setattr(config, "SAVES", tmp_path / "saves")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    fake = FakeCodex(tmp_path / "codex-home", asyncio.create_subprocess_exec)
    monkeypatch.setattr(imagegen, "status", lambda: imagegen.Status(True, "", "codex"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
    return fake


def picture(path: Path, colour=BLUE, size=(800, 500)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path)
    return path


def colour_of(path: str | Path) -> tuple[int, ...]:
    with Image.open(path) as im:
        return im.convert("RGB").resize((1, 1)).getpixel((0, 0))


def near(a, b, slack: int = 24) -> bool:
    return all(abs(x - y) <= slack for x, y in zip(a, b, strict=True))


# -- the codex run -------------------------------------------------------------------------
def test_codex_is_asked_for_one_picture_with_its_own_short_instructions(tmp_path):
    args = imagegen.command("codex", tmp_path, tmp_path / "attached.jpg")
    assert args[:2] == ["codex", "exec"] and args[-1] == "-"  # the prompt on stdin: -i takes any number of files
    assert "--json" in args and "--ignore-user-config" in args and args[args.index("-s") + 1] == "read-only"
    assert args[args.index("-m") + 1] == imagegen.DRIVER  # the login's own default model may not run in exec
    assert args[args.index("-i") + 1] == str(tmp_path / "attached.jpg")
    assert f"model_instructions_file={json.dumps(str(tmp_path / 'instructions.md'))}" in args
    assert "skills.include_instructions=false" in args  # it read its 19 KB imagegen skill first: 128k tokens
    assert {"features.shell_tool=false", "features.plugins=false", "features.image_generation=true"} <= set(args)
    assert "features.code_mode_host=false" not in args  # the image tool is called through the code-mode host
    assert "--ephemeral" not in args  # without a session Codex keeps no picture
    assert "-i" not in imagegen.command("codex", tmp_path)


def test_the_thread_and_the_picture_are_read_off_codex_output(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home"))
    thread = "01a15006-16ca-7401-ad01-cf3d77c0aaf4"
    made = picture(tmp_path / "home" / "generated_images" / thread / "exec-1.png")
    out = "\n".join(json.dumps(x) for x in [
        {"type": "thread.started", "thread_id": thread},
        {"type": "item.completed", "item": {"type": "error", "message": "something optional is unavailable"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": f"`{made}`."}},
        {"type": "turn.completed", "usage": {"input_tokens": 7588, "cached_input_tokens": 2560, "output_tokens": 248,
                                             "reasoning_output_tokens": 40}},
    ]).encode() + b"\nnot json\n"
    found, usage, failure, said = imagegen.events(out)
    assert found == thread and not failure
    assert usage == {"input_tokens": 7588, "cache_read": 2560, "output_tokens": 288}
    assert imagegen.generated(thread, said) == made
    assert imagegen.generated(thread, "done") == made  # not named: the newest picture in the thread's folder
    stray = picture(tmp_path / "elsewhere.png")
    assert imagegen.generated(thread, str(stray)) == made  # a path anywhere else is not taken
    assert imagegen.generated("../../outside", str(stray)) is None


async def test_a_picture_is_stored_small_with_its_credit_chain_and_the_codex_session_is_forgotten(codex, tmp_path):
    original = picture(tmp_path / "art" / "square.jpg", size=(1600, 900))
    (tmp_path / "art" / "square.credit.json").write_text(json.dumps({"title": "Old square", "license": "CC0"}), encoding="utf-8")
    usage = []
    out = await imagegen.make("广场失火", tmp_path / "saves" / "square.look1.jpg", kind="scene", module="example",
                              original=original, title="广场（失火后）", on_reply=usage.append)
    assert out == tmp_path / "saves" / "square.look1.jpg" and near(colour_of(out), RED)
    with Image.open(out) as im:
        assert max(im.size) == imagegen.LONG_SIDE  # 1716 × 916 from Codex, cut down
    assert out.with_name("square.look1.prep.png").is_file()  # preprocessed like every other picture
    credit = json.loads(out.with_name("square.look1.credit.json").read_text(encoding="utf-8"))
    assert credit["license"] == imagegen.LICENSE and credit["title"] == "广场（失火后）"
    assert credit["based_on"] == {"title": "Old square", "license": "CC0"}
    assert max(codex.attached[0]) <= imagegen.LONG_SIDE and codex.prompts == ["广场失火"]
    assert usage[0].usage == {"input_tokens": 7588, "cache_read": 2560, "output_tokens": 248}
    thread = next(c for c in codex.calls if c[1] == "delete")[-1]
    assert ["codex", "delete", "--force", thread] in codex.calls  # out of the user's Codex history
    assert not (tmp_path / "codex-home" / "generated_images" / thread).exists()
    assert imagegen.used("example") == 1


async def test_a_failed_or_slow_picture_is_not_counted(codex, monkeypatch, tmp_path):
    codex.fail = "image generation host is disabled"
    with pytest.raises(imagegen.Failed, match="host is disabled"):
        await imagegen.make("p", tmp_path / "a.jpg", kind="scene", module="m")
    spawned = []

    async def slow(*args, **kwargs):
        if args[1] != "exec":
            return await codex(*args, **kwargs)
        proc = await codex.real(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", slow)
    with pytest.raises(imagegen.Failed, match="超过"):
        await imagegen.make("p", tmp_path / "b.jpg", kind="scene", module="m", timeout=0.5)
    assert spawned[0].returncode is not None  # the process was killed, not left running
    task = asyncio.create_task(imagegen.make("p", tmp_path / "c.jpg", kind="scene", module="m"))
    for _ in range(100):
        if len(spawned) == 2:
            break
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert spawned[1].returncode is not None
    assert imagegen.used("m") == 0 and imagegen.left("m") == imagegen.LIMIT and not (tmp_path / "a.jpg").exists()


async def test_a_module_gets_a_hundred_pictures_and_then_no_more(codex, tmp_path):
    ledger = imagegen.ledger("zhuishuren")
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({"module": "zhuishuren", "used": imagegen.LIMIT - 2, "pictures": []}), encoding="utf-8")
    one, two, three = (imagegen.make("p", tmp_path / f"{n}.jpg", kind="scene", module="zhuishuren") for n in "abc")
    results = await asyncio.gather(one, two, three, return_exceptions=True)  # at once: the third is refused
    assert sum(isinstance(r, Path) for r in results) == 2 and sum(isinstance(r, imagegen.OverBudget) for r in results) == 1
    assert imagegen.used("zhuishuren") == imagegen.LIMIT and imagegen.left("zhuishuren") == 0
    with pytest.raises(imagegen.OverBudget):
        await imagegen.make("p", tmp_path / "d.jpg", kind="scene", module="zhuishuren")
    assert len(codex.prompts) == 2  # a refusal never starts Codex
    assert imagegen.left("another") == imagegen.LIMIT  # the count is per module
    ledger.write_text("{broken", encoding="utf-8")
    assert imagegen.left("zhuishuren") == 0  # an unreadable count spends nothing more


def test_without_a_logged_in_codex_that_can_draw_the_feature_is_off(monkeypatch):
    check = REAL_STATUS.__wrapped__  # past the cache

    def run(answers):
        def fake(args, **kwargs):
            code, out, err = answers[args[1]]
            return subprocess.CompletedProcess(args, code, out.encode(), err.encode())
        return fake

    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert not check().ok and "codex" in check().reason
    monkeypatch.setattr(shutil, "which", lambda name: "/opt/codex")
    listed = "image_generation                         stable             true\n"
    monkeypatch.setattr(subprocess, "run", run({"login": (1, "", "Not logged in"), "features": (0, listed, "")}))
    assert not check().ok and "登录" in check().reason
    monkeypatch.setattr(subprocess, "run", run({"login": (0, "", "Logged in using ChatGPT"),
                                                "features": (0, "image_generation  stable  false\n", "")}))
    assert not check().ok and "image_generation" in check().reason
    monkeypatch.setattr(subprocess, "run", run({"login": (0, "", "Logged in using ChatGPT"), "features": (0, listed, "")}))
    assert check() == imagegen.Status(True, "", "/opt/codex")
    assert imagegen.why_off(config.Config(imagegen=False)) == "设置里关掉了" and not imagegen.enabled(None)


async def test_with_codex_unavailable_nothing_is_made_and_nothing_breaks(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SAVES", tmp_path / "saves")
    with pytest.raises(imagegen.Unavailable):
        await imagegen.make("p", tmp_path / "a.jpg", kind="scene", module="m")
    m = Module.load(EXAMPLE)
    assert imagegen.filler(config.Config(), m, config.LOCAL_MODULES / "x" / "art") is None
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    game = Game(m, state, FakeCast({}), Rules())
    game.visuals.cfg = config.Config()
    assert game.visuals.look("square", "广场被淹了") == "square"
    game.visuals.schedule()
    assert not game.visuals.tasks and state.looks["square"]["status"] == "pending"  # drawn once Codex is there
    await game.close()


# -- the story changes a place or a person ------------------------------------------------------
BURN = Improv(kind="后果", reason="他点着了告示栏", looks=[NewLook(target="square", change="告示栏烧成焦黑的木架，石板上落满灰烬")],
              note="告示栏烧起来了")


def test_a_change_must_name_an_existing_place_or_person():
    m = Module.load(EXAMPLE)
    assert improv.check(m, BURN, at="square") == []
    wrong = BURN.model_copy(update={"looks": [NewLook(target="nowhere", change="烧了")]})
    assert any("nowhere" in p for p in improv.check(m, wrong, at="square"))
    assert improv.apply(m, BURN).looks == [("square", "告示栏烧成焦黑的木架，石板上落满灰烬")]


async def test_a_place_the_story_burns_gets_a_redrawn_picture_that_the_save_keeps(codex, fake, tmp_path):
    m = Module.load(EXAMPLE)
    original = picture(tmp_path / "art" / "square.jpg")
    m.pictures["square"] = str(original)
    arson = Action(kind="other", summary="周衡点着了告示栏")
    script = {"x": (arson, Ruling(needs_roll=False, minutes=5, scope="出格", reason="放火"))}
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="burn")
    events = []

    async def emit(ev):
        events.append(ev)

    game = Game(m, state, Improvising(script, [BURN]), Rules(dice=fake), emit)
    game.visuals.cfg = config.Config()
    await game.resolve(arson, await game.rule(arson))
    await asyncio.gather(*(t for k, t in game.visuals.tasks.items() if k.startswith("look:")))
    record = state.looks["square"]
    assert record["status"] == "ready" and record["target"] == "location:square" and record["made"] == "1"
    assert m.pictures["square"] == record["picture"] != str(original) and near(colour_of(record["picture"]), RED)
    assert Path(record["picture"]).is_relative_to(config.SAVES)  # per save: the module itself never changes
    assert codex.attached and "告示栏烧成焦黑的木架" in codex.prompts[0] and "周衡" not in codex.prompts[0]
    shown = [e for e in events if e["type"] == "art" and e["key"] == "square"]
    assert shown[-1]["picture"] == record["picture"] and shown[-1]["art"] == m.art["square"]
    assert state.usage["visual:imagegen"]["input"] == 7588
    saved = GameState.model_validate_json(state.model_dump_json())
    loaded = Module.load(EXAMPLE)
    loaded.pictures["square"] = str(original)
    again = Game(loaded, saved, FakeCast({}), Rules(dice=fake))
    again.visuals.cfg = config.Config()
    again.visuals.schedule()
    assert loaded.pictures["square"] == record["picture"] and not again.visuals.tasks
    assert len(codex.prompts) == 1  # nothing drawn twice
    await game.close()
    await again.close()


async def test_a_change_that_comes_while_one_is_drawn_goes_on_top_and_a_game_stops_at_its_cap(codex, tmp_path):
    m = Module.load(EXAMPLE)
    m.pictures["warden"] = str(picture(tmp_path / "art" / "warden.jpg"))
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="wounds")
    game = Game(m, state, FakeCast({}), Rules())
    game.visuals.cfg = config.Config()
    game.visuals.look("warden", "左臂缠着渗血的绷带")
    game.visuals.schedule()
    game.visuals.look("npc:warden", "脸色灰白，靠着墙才站得住")  # while the first is drawn
    for _ in range(3):
        await asyncio.gather(*(t for k, t in list(game.visuals.tasks.items()) if k.startswith("look:")))
    record = state.looks["warden"]
    assert record["made"] == "2" and record["status"] == "ready" and record["kind"] == "portrait"
    assert "左臂" in codex.prompts[0] and "脸色灰白" in codex.prompts[1] and "驼背" in codex.prompts[0]
    assert m.pictures["warden"].endswith("warden.look2.jpg") and game.visuals.made() == 2
    record.update(made=str(imagegen.PER_GAME))
    game.visuals.look("warden", "倒在地上不动了")
    game.visuals.schedule()
    assert not any(not t.done() for k, t in game.visuals.tasks.items() if k.startswith("look:"))
    assert state.looks["warden"]["status"] == "pending" and len(codex.prompts) == 2
    await game.close()


async def test_a_module_s_own_change_shows_its_picture_once_its_flag_is_set(fake, tmp_path):
    m = Module.load(EXAMPLE)
    m.location("square").looks = [Look(when="flag:bell_stopped", change="钟楼的影子里一片死寂", art="square_still")]
    m.pictures["square_still"] = str(picture(tmp_path / "art" / "square_still.jpg", RED))
    events = []

    async def emit(ev):
        events.append(ev)

    state = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="still")
    game = Game(m, state, FakeCast({}), Rules(dice=fake), emit)  # no Codex needed: drawn at import
    state.flags.append("bell_stopped")
    game.visuals.schedule()
    await asyncio.gather(*game.visuals.tasks.values())
    assert m.pictures["square"] == m.pictures["square_still"] and state.looks["square"]["applied"] == "flag:bell_stopped"
    assert [e["key"] for e in events if e["type"] == "art"] == ["square"]
    game.visuals.schedule()
    await asyncio.gather(*game.visuals.tasks.values())
    assert len([e for e in events if e["type"] == "art"]) == 1  # shown once
    await game.close()


async def test_lan_players_see_the_redrawn_picture_and_a_late_joiner_gets_it_too(codex, fake, tmp_path):
    hub = Hub(EXAMPLE, seats=["玩家"], cast=TableCast(), rules=Rules(dice=fake))
    hub.game.visuals.cfg = config.Config()
    hub.m.pictures["square"] = str(picture(tmp_path / "art" / "square.jpg"))
    hub.m.location("square").looks = [Look(when="flag:bell_stopped", change="钟楼塌了半边")]
    sent = []

    async def collect(msg):
        sent.append(msg)

    hub._broadcast = collect
    assert all(loc["looks"] == [] for loc in table(hub.m, hub.state)["locations"])  # what will happen stays home
    hub.game.visuals.look("square", "广场被洪水淹了，水没过台阶")
    hub.game.visuals.schedule()
    await asyncio.gather(*(t for k, t in hub.game.visuals.tasks.items() if k.startswith("look:")))
    event = next(msg["ev"] for msg in sent if msg["t"] == "event" and msg["ev"]["type"] == "art")
    assert event["key"] == "square" and "picture" not in event  # no host path, the picture itself
    assert near(colour_of(media.unpack(event["picture_data"])), RED)
    assert "洪水" not in json.dumps(sent, ensure_ascii=False)  # the change itself is the keeper's
    welcome = hub._welcome("late")
    assert "looks" not in welcome["state"] and near(colour_of(table_module(welcome).pictures["square"]), RED)
    await hub.game.close()


# -- import -------------------------------------------------------------------------------------
async def test_an_import_draws_what_no_photo_was_found_for_and_the_states_the_module_describes(codex, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOCAL_MODULES", tmp_path / "local")
    work = config.LOCAL_MODULES / "example"
    shutil.copytree(EXAMPLE, work)
    raw = yaml.safe_load((work / "module.yaml").read_text(encoding="utf-8"))
    raw["locations"][0]["looks"] = [{"when": "flag:bell_stopped", "change": "钟停了，广场上的人都抬头望着它"}]
    (work / "module.yaml").write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(imagery, "find_picture", nothing)
    monkeypatch.setattr(imagery, "available", lambda: True)
    m = Module.load(work)
    assert not [i for i in graph.audit(m) if i.level == "error"]
    log = io.StringIO()
    done = await find_photos(m, work, config.Config(), Console(file=log, width=200))
    assert sorted(done) == sorted(name for name, _, _ in imagery.targets(m))
    drawn = work / "art" / "square_bell_stopped.jpg"
    assert drawn.is_file() and "square_bell_stopped" in Module.load(work).pictures
    credit = json.loads((work / "art" / "square_bell_stopped.credit.json").read_text(encoding="utf-8"))
    assert credit["based_on"]["license"] == imagegen.LICENSE  # made from the square's own (generated) picture
    assert imagegen.used("example") == len(done) + 1 and "Codex 照描述画" in log.getvalue()
    # the repository's own modules never get generated pictures
    assert imagegen.filler(config.Config(), m, config.MODULES / "zhuishuren" / "art") is None
    assert await imagegen.looks(config.Config(), m, config.MODULES / "zhuishuren" / "art") == []


def test_a_module_s_changes_are_checked():
    m = Module.load(EXAMPLE)
    m.location("square").looks = [Look(when="visit:square", change="x"), Look(when="flag:nobody", change="y")]
    m.npc("preacher").looks = [Look(when="flag:bell_stopped", change="z", art="Bad Key")]
    m.location("tower").looks = [Look(when="flag:bell_stopped", change="塌了")]  # no art key of its own
    errors = [i.text for i in graph.audit(m) if i.level == "error"]
    assert any("visit:square" in e for e in errors) and any("flag:nobody" in e for e in errors)
    assert any("Bad Key" in e for e in errors) and any("tower" in e and "图片键" in e for e in errors)


def test_doctor_says_whether_story_pictures_can_be_made(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from typer.testing import CliRunner

    from autokeeper.cli import app
    from autokeeper.rules import bcdice

    class Dice:
        version = "stand-in"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def roll(self, expr):
            return SimpleNamespace(text=f"{expr} ＞ 37")

    monkeypatch.setattr(config, "SAVES", tmp_path)
    monkeypatch.setattr(bcdice, "BCDice", Dice)
    assert "· 剧情配图（Codex 生图）：关闭（测试里不生图）" in CliRunner().invoke(app, ["doctor"]).output
    monkeypatch.setattr(imagegen, "status", lambda: imagegen.Status(True, "", "codex"))
    out = CliRunner().invoke(app, ["doctor"]).output
    assert f"✓ 剧情配图（Codex 生图）：可用，每个模组最多 {imagegen.LIMIT} 张" in out


async def test_improvised_people_places_and_props_no_photo_was_found_for_are_drawn_by_codex(codex, monkeypatch):
    from autokeeper import artkit
    from autokeeper.agents import artist
    from autokeeper.engine.visuals import Visuals

    from .test_visuals import scene

    async def nothing(*args, **kwargs):
        return None

    async def draw(cfg, subject, *, portrait=False, on_reply=None):
        return "[]", artkit.auto_mask("[]")

    monkeypatch.setattr(imagery, "find_picture", nothing)
    monkeypatch.setattr(artist, "draw", draw)
    m, st = scene()
    visuals = Visuals(m, st, config.Config(), lambda ev: asyncio.sleep(0))
    visuals.schedule()
    await asyncio.gather(*visuals.tasks.values())
    assert all(r["generated"] == "1" and Path(r["picture"]).is_file() for r in st.visuals.values())
    assert visuals.made() == 3 and all(m.pictures[key] == r["picture"] for key, r in st.visuals.items())
    assert not any("有秘密" in p for p in codex.prompts) and any("围裙上沾着咖啡渍" in p for p in codex.prompts)
    await visuals.close()
