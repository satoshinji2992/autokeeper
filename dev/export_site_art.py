"""Export the game's title renderer as browser character cells (requires chafa)."""

import argparse
import json
import os
import shutil
from pathlib import Path

from rich.color import Color

from autokeeper import paths
from autokeeper.tui.drawing import art, cover


def color_index(value: Color | None, palette: list[str], indices: dict[str, int]) -> int:
    rgb = value.get_truecolor() if value else None
    text = f"#{rgb.red:02x}{rgb.green:02x}{rgb.blue:02x}" if rgb else "#060403"
    if text not in indices:
        indices[text] = len(palette)
        palette.append(text)
    return indices[text]


def export(destination: Path) -> None:
    if not shutil.which("chafa"):
        raise SystemExit("Install chafa before exporting the title artwork.")
    os.environ["COLORTERM"] = "truecolor"
    result = {
        "logos": {"small": list(art.title_lines(80)), "wide": list(art.title_lines(120))},
        "scenes": {},
    }
    for name, cols, rows in [("wide", 150, 66), ("tall", 66, 82)]:
        frame = cover.frame(cols, rows, cover.focus(cols, rows, round(rows * .28)), "photo", 0)
        palette: list[str] = []
        indices: dict[str, int] = {}

        packed = [
            [[char or " ", color_index(style.color, palette, indices), color_index(style.bgcolor, palette, indices)]
             for char, style in row]
            for row in frame
        ]
        result["scenes"][name] = {"cols": cols, "rows": len(packed), "palette": palette, "cells": packed}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Exported title character cells to {destination} ({destination.stat().st_size} bytes)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=paths.DEV_OUTPUT / "site" / "cover-data.json")
    export(parser.parse_args().output)
