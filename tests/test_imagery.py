"""Pictures as character art: the crop that fills the panel, the outline strokes and how they
sit on chafa's output. Everything but the last test runs without chafa and without OpenCV."""

from __future__ import annotations

import shutil

import numpy as np
import pytest
from PIL import Image, ImageDraw
from rich.text import Text

from autokeeper import imagery
from autokeeper.atmosphere import grid

COLS, ROWS = 40, 10
CW, CH = imagery.CELL_PX


def scene(*shapes: tuple[str, list]) -> Image.Image:
    """A dark picture with the panel's shape (COLS × ROWS cells) and light shapes on it."""
    im = Image.new("RGB", (COLS * CW, ROWS * CH), (40, 44, 40))
    draw = ImageDraw.Draw(im)
    for kind, xy in shapes:
        getattr(draw, kind)(xy, fill=(225, 220, 205))
    return im


def strokes_of(im: Image.Image) -> str:
    return "".join(sorted(set(imagery.outlines(im, COLS, ROWS).ravel()) - {""}))


def test_strokes_follow_the_direction_of_the_line():
    mid = 20 * CW + CW // 2  # the edge runs through the middle of column 20
    vertical = imagery.outlines(scene(("rectangle", [mid, 0, COLS * CW, ROWS * CH])), COLS, ROWS)
    assert (vertical[1:-1, 20] == "|").all()
    assert (np.delete(vertical, 20, axis=1) == "").all()  # one stroke wide, nothing elsewhere
    assert strokes_of(scene(("rectangle", [0, 5 * CH + CH // 2, COLS * CW, ROWS * CH]))) == "-"
    assert strokes_of(scene(("rectangle", [0, 5 * CH + CH - 3, COLS * CW, ROWS * CH]))) == "_"  # low in its row
    w, h = COLS * CW, ROWS * CH
    rising = imagery.outlines(scene(("polygon", [(w // 2 - h // 2, h), (w // 2 + h // 2, 0), (w, 0), (w, h)])), COLS, ROWS)
    falling = imagery.outlines(scene(("polygon", [(w // 2 - h // 2, 0), (w // 2 + h // 2, h), (w, h), (w, 0)])), COLS, ROWS)
    assert (rising == "/").sum() >= ROWS - 2 and (rising == "/").sum() >= 0.8 * (rising != "").sum()
    assert (falling == "\\").sum() >= ROWS - 2 and (falling == "\\").sum() >= 0.8 * (falling != "").sum()


def test_flat_pictures_and_grain_get_no_strokes():
    assert strokes_of(scene()) == ""
    grain = Image.fromarray(np.random.default_rng(1).integers(0, 255, (ROWS * CH, COLS * CW, 3), dtype=np.uint8))
    assert (imagery.outlines(grain, COLS, ROWS) != "").mean() < 0.05  # leaves and grain point every way


def test_scene_fills_the_panel_with_the_part_that_has_structure():
    tall = Image.new("RGB", (300, 600), (70, 90, 60))
    draw = ImageDraw.Draw(tall)
    for x in range(10, 300, 40):  # a row of gravestones low in a tall photo
        draw.rectangle([x, 450, x + 18, 540], fill=(225, 220, 205))
    framed = imagery.frame(tall, 80, 15)
    assert abs(framed.width / framed.height - 80 * 0.5 / 15) < 0.02  # the panel's shape
    assert np.asarray(framed.convert("L")).max() > 200  # the gravestones are in it
    wide = Image.new("RGB", (1200, 200), (70, 90, 60))
    ImageDraw.Draw(wide).rectangle([1000, 40, 1030, 200], fill=(225, 220, 205))
    framed = imagery.frame(wide, 80, 15)
    assert framed.height == 200 and np.asarray(framed.convert("L")).max() > 200  # slid sideways to it
    boxed = imagery.frame(tall, 400, 15)
    assert abs(boxed.width / boxed.height - 4.0) < 0.02  # a very wide panel gets a letterbox


CHAFA_LIKE = (  # how chafa writes cells: an SGR only when the colours change, a reset per line
    "\x1b[0m\x1b[38;2;16;32;48;48;2;64;80;96m▀▀\x1b[38;2;1;2;3m▄\x1b[48;2;4;5;6m▄\x1b[0m\n"
    "\x1b[38;2;170;187;204;48;2;17;34;51m▌▐▌▐\x1b[0m"
)


def test_overlay_draws_the_strokes_and_keeps_everything_else():
    strokes = np.array([["", "|", "/", ""], ["", "", "", "_"]])
    under = np.full((2, 4, 3), 200, dtype=np.uint8)
    colour = np.full((2, 4, 3), 30, dtype=np.uint8)
    out = grid(Text.from_ansi(imagery.overlay(CHAFA_LIKE, strokes, under, colour)), 4, 2)
    before = grid(Text.from_ansi(CHAFA_LIKE), 4, 2)
    assert ["".join(ch for ch, _ in row) for row in out] == ["▀|/▄", "▌▐▌_"]
    for y, x in [(0, 0), (0, 3), (1, 0), (1, 2)]:  # the rest keep chafa's colours, even after a stroke
        assert out[y][x][1].color.triplet == before[y][x][1].color.triplet
        assert out[y][x][1].bgcolor.triplet == before[y][x][1].bgcolor.triplet
    for y, x in [(0, 1), (0, 2), (1, 3)]:  # strokes: ink on the picture's colour
        assert out[y][x][1].color.triplet == (30, 30, 30) and out[y][x][1].bgcolor.triplet == (200, 200, 200)


def test_ink_stands_off_its_cell_by_day_and_after_dark():
    stone, shadow = [230, 225, 210], [20, 22, 30]
    cells = np.array([[stone, shadow]], dtype=np.uint8)
    day, night = imagery.ink(cells, "上午"), imagery.ink(cells, "深夜")
    assert day[0, 0].sum() < sum(stone) / 3  # dark strokes on a light stone
    assert day[0, 1].sum() > 4 * sum(shadow)  # light strokes in the shadows
    assert 3 * sum(shadow) < night[0, 1].sum() < day[0, 1].sum()  # dimmer after dark, still readable


@pytest.mark.skipif(shutil.which("chafa") is None, reason="chafa not installed")
def test_outlines_are_drawn_into_every_weather_layer(tmp_path):
    img = tmp_path / "scene.png"
    im = Image.new("RGB", (480, 180), (60, 70, 60))
    ImageDraw.Draw(im).rectangle([200, 40, 290, 180], fill=(225, 220, 205))  # a gravestone
    im.save(img)

    def art(style: str, graded: str = "") -> Text:
        return Text.from_ansi(imagery.render(str(img), 48, 12, style, "上午", True, graded))

    plain, lined, dark = art("photo"), art("picture"), art("picture", "dim")
    assert [ln.cell_len for ln in lined.split("\n")] == [ln.cell_len for ln in plain.split("\n")]
    assert "|" in lined.plain and not set(plain.plain) & set("|/-\\_")
    marks = [i for i, ch in enumerate(lined.plain) if ch in "|/-\\_"]
    assert marks == [i for i, ch in enumerate(dark.plain) if ch in "|/-\\_"]  # the same lines in the torch's dark
    rows, dim_rows = grid(lined, 48, 12), grid(dark, 48, 12)
    y, x = next((y, x) for y, row in enumerate(rows) for x, (ch, _) in enumerate(row) if ch == "|")
    assert sum(dim_rows[y][x][1].color.triplet) < sum(rows[y][x][1].color.triplet)  # only darker


def test_a_creature_is_painted_with_its_silhouette_and_staged_bigger_the_nearer(tmp_path):
    from autokeeper import scenery

    beast = scenery.creature("食尸鬼", "带犬科特征、利爪代替手指", key="ghoul")
    assert beast.mode == "RGBA"
    alpha = np.asarray(beast.getchannel("A"))
    assert alpha[5, 5] < 10 and alpha[beast.height // 3, beast.width // 2] > 200  # the dark round it is not part of it
    path = tmp_path / "beast.png"
    beast.save(path)
    Image.new("RGB", (320, 180), (90, 120, 90)).save(tmp_path / "place.png")
    arena = imagery.staged(str(path), 40, 12)
    assert arena.size == (320, 192)  # the stage's own shape: cells half as wide as tall

    def figure(near: float) -> int:  # columns where it stands out from the place
        im = np.asarray(imagery.staged(str(path), 40, 12, str(tmp_path / "place.png"), near), float)
        return int((np.abs(im - im[:, :8].mean(axis=(0, 1))).sum(axis=2).max(axis=0) > 90).sum())

    assert figure(0.2) < figure(1.0)


def test_a_figure_drawn_on_black_is_cut_out_with_the_dark_inside_it():
    im = Image.new("RGB", (200, 260), (2, 2, 2))
    draw = ImageDraw.Draw(im)
    draw.ellipse((40, 30, 160, 230), fill=(150, 160, 120))
    draw.ellipse((80, 90, 120, 130), fill=(0, 0, 0))  # a socket: dark, but part of it
    mask = np.asarray(imagery.silhouette(im))
    assert mask[5, 5] < 20 and mask[110, 100] > 200 and mask[200, 100] > 200
    assert imagery.silhouette(Image.new("RGB", (200, 260), (200, 190, 170))) is None  # a light wall: nothing to cut
