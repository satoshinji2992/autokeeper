"""编辑 a card of the player's own (角色卡 → 我的角色): everything on it that is not a number.
Name, occupation, age, sex, where they live and were born, the backstory, what they carry,
and the photo. Characteristics, skills, HP, SAN, MP and luck come from the rules and stay as
they are; the form has no box for them."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static, TextArea

from ...rules.coc import Investigator

# The paper sheet's backstory, in its order; a card's other fields come after them.
BACKSTORY = ("个人描述", "思想与信念", "重要之人", "意义非凡之地", "宝贵之物", "特质",
             "伤口和疤痕", "恐惧症和躁狂症", "神秘学典籍、咒文和神器", "第三类接触")  # fmt: skip
FIELDS = (("name", "名字"), ("occupation", "职业"), ("age", "年龄"), ("sex", "性别"), ("residence", "住在"), ("birthplace", "生于"))


def backstory_keys(inv: Investigator) -> list[str]:
    return [*BACKSTORY, *(k for k in inv.backstory if k not in BACKSTORY)]


class CardEditModal(ModalScreen[dict | None]):
    """Dismisses with the changes ({"investigator": fields, "photo": a picture file or None}),
    or None on Esc. Nothing is written here; tui/screens/cards.py saves them."""

    BINDINGS = [Binding("escape", "dismiss(None)", "取消"), Binding("ctrl+s", "keep", "保存")]

    def __init__(self, inv: Investigator, has_photo: bool):
        super().__init__()
        self.inv, self.has_photo = inv, has_photo

    def compose(self) -> ComposeResult:
        inv = self.inv
        with VerticalScroll(id="card-edit", classes="modal"):
            yield Static(f"编辑 {inv.name}", classes="modal-title")
            yield Static(Text("属性与技能数值不可手动修改。", style="dim"), classes="edit-note")
            for key, label in FIELDS:
                with Horizontal(classes="edit-row"):
                    yield Static(label, classes="edit-label")
                    yield Input(str(getattr(inv, key)), id=f"edit-{key}", compact=True, type="integer" if key == "age" else "text")
            yield Static("背景", classes="edit-head")
            for i, key in enumerate(backstory_keys(inv)):
                with Horizontal(classes="edit-row edit-tall"):
                    yield Static(key, classes="edit-label")
                    yield TextArea(inv.backstory.get(key, ""), id=f"edit-story-{i}", compact=True, soft_wrap=True, show_line_numbers=False)
            with Horizontal(classes="edit-row edit-tall"):
                yield Static("随身物品", classes="edit-label")
                yield TextArea("\n".join(inv.inventory), id="edit-inventory", compact=True, soft_wrap=True)
            with Horizontal(classes="edit-row"):
                yield Static("照片", classes="edit-label")
                yield Input(id="edit-photo", compact=True,
                            placeholder="拖入图片文件以更换照片" + ("；留空保留当前照片" if self.has_photo else ""))  # fmt: skip
            with Horizontal(classes="buttons"):
                yield Button("保存", id="edit-save", variant="primary")
                yield Button("取消", id="edit-cancel")
            yield Static("Tab 下一栏 · Ctrl+S 保存 · Esc 取消", classes="system")

    def on_mount(self) -> None:
        self.query_one("#edit-name", Input).focus()

    @on(Button.Pressed, "#edit-cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#edit-save")
    def action_keep(self) -> None:
        from ...agents.card import local_file

        values = {key: self.query_one(f"#edit-{key}", Input).value.strip() for key, _ in FIELDS}
        if not values["name"]:
            self.app.notify("姓名不能为空。", severity="warning")
            return
        try:
            age = int(values["age"])
        except ValueError:
            age = 0
        if not 1 <= age <= 120:
            self.app.notify("年龄须为整数，例如 30。", severity="warning")
            return
        values["age"] = age
        story = {}
        for i, key in enumerate(backstory_keys(self.inv)):
            if text := self.query_one(f"#edit-story-{i}", TextArea).text.strip():
                story[key] = text
        values["backstory"] = story
        values["inventory"] = [line.strip() for line in self.query_one("#edit-inventory", TextArea).text.splitlines() if line.strip()]
        photo: Path | None = None
        if typed := self.query_one("#edit-photo", Input).value.strip():
            photo = local_file(typed)
            if photo is None:
                self.app.notify("未找到图片，请拖入文件或填写完整路径。", severity="warning")
                return
        self.dismiss({"investigator": values, "photo": photo})
