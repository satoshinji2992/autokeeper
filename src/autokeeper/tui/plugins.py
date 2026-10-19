"""The Ctrl+K window: what each extra rule does, in the player's terms.

For each active rule it shows the plain-words description, the custom actions a player can try
and when, the rule's own public status line if it offers one (风寒：第 2 天), the recent public
effects it produced this game, and, when it is paused, why — in plain words, never a traceback.
Private counters, source code and keeper-only state never reach this screen.

The host (and a solo player) can pause or re-enable a rule and add a house rule in plain words;
the author agent writes it, the sandbox runs its tests, and only a passing rule is installed
into this save (never into the module files). A LAN client sees the same public information,
and its house-rule request is drafted and installed by the host.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from ..engine.state import GameState
from ..module.schema import Module
from ..plugins.schema import does

Drafter = Callable[[str, Callable[[str], Awaitable[None]]], Awaitable[tuple[bool, str]]]


def _rows(module: Module, state: GameState, *, manage: bool) -> list[dict[str, Any]]:
    """One public view per active rule. On the host it reads the loaded manifests; a LAN client
    reads the redacted summaries the hub shared (module.plugin_info), and its status and recent
    effects come from the synced state, keyed by the rule's public name."""
    rows: list[dict[str, Any]] = []
    if manage:
        for identity in module.plugins:
            plugin = module.extensions.get(identity)
            if plugin is None:
                rows.append({"name": identity, "version": "未加载", "description": module.extension_errors.get(identity, ""),
                             "actions": [], "commands": [], "does": [], "paused": True, "error": "这条规则没能载入",
                             "id": identity, "loaded": False})
                continue
            rows.append({
                "name": plugin.name, "version": plugin.version, "description": plugin.description,
                "actions": [(a.name, a.description) for a in plugin.actions],
                "commands": [(c.name, c.description) for c in plugin.commands], "does": does(plugin.capabilities),
                "paused": identity in state.plugin_errors, "error": state.plugin_errors.get(identity, ""),
                "id": identity, "loaded": True,
            })
    else:
        for info in module.plugin_info:
            rows.append({
                "name": info.get("name", ""), "version": info.get("version", ""),
                "description": info.get("description", ""), "actions": [tuple(a) for a in info.get("actions", [])],
                "commands": [tuple(c) for c in info.get("commands", [])], "does": list(info.get("does", [])),
                "paused": bool(info.get("paused")), "error": "", "id": None, "loaded": True,
            })
    for row in rows:
        row["status"] = state.plugin_status.get(row["name"], "")
        row["recent"] = [e.get("line", "") for e in state.plugin_log if e.get("name") == row["name"]][-3:]
    return rows


class PluginsModal(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "返回")]

    def __init__(self, module: Module, state: GameState, *, manage: bool = True,
                 drafter: Drafter | None = None, send: Callable[[str], None] | None = None):
        super().__init__()
        self.module, self.state, self.manage = module, state, manage
        self.drafter, self.send = drafter, send
        self.working = False

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal"):
            yield Static("规则插件", classes="modal-title")
            lead = ("本局之外的额外规则。暂停后会保留已有状态。" if self.manage else
                    "本局的额外规则在你的电脑上运行，结果会同步给所有人。" if self.send else
                    "本局的额外规则由房主管理，结果会同步到这里；想加家规，跟房主说一声。")
            yield Static(Text(lead, style="dim"), id="plugin-lead")
            yield from self._list()
            if self.drafter or self.send:
                yield Static(Text("\n加一条家规（用大白话说，例如：每过一夜要吃一顿饭，不吃 HP 减 1）", style="bold"))
                yield Input(placeholder="说出你想要的规则，回车添加", id="house-rule")
                yield Static("", id="house-status")
            yield Button("返回", id="plugin-back")

    def _list(self) -> ComposeResult:
        # Built without a `with` block so it can also be re-run by _refresh(), outside compose.
        rows = _rows(self.module, self.state, manage=self.manage)
        if not rows:
            yield Static("本局没有自定义插件。", classes="plugin-card")
            return
        for i, row in enumerate(rows):
            yield Static(self._card(row), classes="plugin-card")
            if self.manage and row["loaded"]:
                label = "启用 / 重试" if row["paused"] else "暂停"
                yield Horizontal(Button(label, id=f"plugin-{i}"), classes="buttons plugin-card")

    def _card(self, row: dict[str, Any]) -> Text:
        t = Text()
        t.append(f"{row['name']} · {row['version']}", style="bold")
        if row["paused"]:
            t.append("  （已暂停）", style="bold red")
        if row["status"]:
            t.append(f"   {row['status']}", style="bold #e9c46a")
        if row["description"]:
            t.append(f"\n{row['description']}", style="")
        if row["actions"]:
            t.append("\n可以尝试：", style="bold")
            for _name, desc in row["actions"]:
                t.append(f"\n  · {_when(desc)}", style="dim")
        if row["commands"]:
            t.append("\n可以输入：", style="bold")
            for name, desc in row["commands"]:
                t.append(f"\n  · /{name}  {desc}", style="dim")
        if row["does"]:
            t.append("\n它会：", style="bold")
            t.append("、".join(row["does"]), style="dim")
            if row["name"] in self.state.plugin_panels:
                titles = "、".join(p.get("title", "") for p in self.state.plugin_panels[row["name"]])
                t.append(f"（右栏的「{titles}」就是它的）", style="dim")
        if row["recent"]:
            t.append("\n最近：", style="bold")
            for line in row["recent"]:
                t.append(f"\n  {line}", style="dim")
        if row["paused"] and row["error"]:
            t.append(f"\n暂停原因：{_plain(row['error'])}", style="red")
        return t

    @on(Input.Submitted, "#house-rule")
    def add_rule(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or self.working:
            return
        if self.send is not None:  # LAN: the host drafts and installs, the result syncs back
            self.send(text)
            self.query_one("#house-rule", Input).value = ""
            self._status("已发给房主，装好后会在这里更新；关掉再打开即可看到。")
        elif self.drafter is not None:
            self._draft(text)

    @work(exclusive=True, group="house-rule")
    async def _draft(self, text: str) -> None:
        assert self.drafter is not None
        self.working = True
        box = self.query_one("#house-rule", Input)
        box.disabled = True

        async def progress(message: str) -> None:
            self._status(message)

        try:
            ok, message = await self.drafter(text, progress)
        except Exception as exc:  # the house rule is a bonus; a failed call must not break the screen
            ok, message = False, f"没能添加（{exc.__class__.__name__}），稍后再试"
        self.working = False
        box.disabled = False
        if ok:
            box.value = ""
            self._refresh()
        self._status(message + ("" if ok else "。可以改一句话再试一次。"))

    def _status(self, text: str) -> None:
        if self.is_mounted:
            self.query_one("#house-status", Static).update(Text(text, style="bold"))

    def _refresh(self) -> None:
        """Rebuild the list in place after a rule is installed, without closing the window."""
        scroll = self.query_one(VerticalScroll)
        for card in list(self.query(".plugin-card")):
            card.remove()
        scroll.mount_all(list(self._list()), after=self.query_one("#plugin-lead"))

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        if event.button.id == "plugin-back":
            self.dismiss()
            return
        if not (event.button.id or "").startswith("plugin-"):
            return
        identity = self.module.plugins[int(event.button.id.removeprefix("plugin-"))]
        if identity in self.state.plugin_errors:
            del self.state.plugin_errors[identity]
        else:
            self.state.plugin_errors[identity] = "已手动暂停"
        event.button.label = "启用 / 重试" if identity in self.state.plugin_errors else "暂停"


def plugin_commands(module: Module, state: GameState) -> list[tuple[str, str]]:
    """The slash commands the active rule plugins add (/占卜 …), as (name, description), for the
    command box's completion, the Ctrl+K window and the in-game menu. A LAN screen reads them
    from the hub's public summaries."""
    out: list[tuple[str, str]] = []
    if module.extensions:
        for identity in module.plugins:
            plugin = module.extensions.get(identity)
            if plugin is not None and identity not in state.plugin_errors:
                out += [(c.name, c.description) for c in plugin.commands]
    else:
        for info in module.plugin_info:
            if not info.get("paused"):
                out += [(name, desc) for name, desc in info.get("commands", [])]
    return list(dict.fromkeys(out))


def action_hints(module: Module, state: GameState) -> list[str]:
    """Short player-facing hints for the active rule plugins' custom actions, for the command
    box's completion and F5. Paused rules and ones that failed to load are left out."""
    out: list[str] = []
    for identity in module.plugins:
        plugin = module.extensions.get(identity)
        if plugin is None or identity in state.plugin_errors:
            continue
        out += [_when(action.description) for action in plugin.actions]
    return [hint for hint in dict.fromkeys(out) if hint]


def _when(description: str) -> str:
    """The first clause of a keeper-facing action description, as a hint of when it applies."""
    for mark in ("。", "；", "\n"):
        description = description.split(mark)[0]
    return description.strip()[:90]


def _plain(error: str) -> str:
    """A paused rule's reason in plain words: the message only, never a traceback."""
    line = error.strip().splitlines()[0] if error.strip() else "出了点问题"
    return line[:120]
