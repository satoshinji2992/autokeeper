"""Exercise the complete keeper with human-written actions and keep a reviewable transcript."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from .. import config
from ..agents.cast import Cast
from ..engine import graph
from ..engine.game import Game
from ..module.schema import Module
from ..rules.coc import Rules

ARRIVAL = [
    "我先站在车站门口看看街上的生活，不急着赶路。",
    "我向站务员问好，问他平常这个时候忙不忙。",
    "我问站务员在哪里能买到早餐，想先吃点热的。",
    "我向他道谢，沿路慢慢往托马斯家走。",
    "我先在这里停一停，看看有什么店铺正在开门。",
    "我暂时不谈案子，想找个地方喝杯咖啡。",
    "我问店里的人在小镇住了多久，日子过得怎么样。",
    "我问能否替我留一份明天的报纸，明早过来拿。",
    "我告辞，直接按信上的地址赶到托马斯家。",
    "我向托马斯打招呼，先问他这几天过得怎么样。",
    "我告诉他旅途顺利，问能不能放下行李、喝口水再谈。",
    "我请他从自己最想说的地方讲起，先听，不替他下结论。",
]


async def run(module: Path, actions: list[str], out: Path, *, timeout: float = 120) -> dict:
    cfg = replace(config.load(), visual_limit=0, imagegen=False, showcase=False)  # story evaluation has no incidental illustration calls
    m = Module.load(module)
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="experience-eval")
    cast = Cast(cfg, m)
    entries, rounds = [], []

    async def emit(ev):
        if ev["type"] not in ("keeper_delta", "status", "echo"):
            entries.append(ev)

    game = Game(m, st, cast, Rules(), emit)
    app = graph.build(game, InMemorySaver())
    thread = {"configurable": {"thread_id": "experience"}}
    checkpoint = out.with_suffix(".checkpoint.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    game.checkpoint = lambda: checkpoint.write_text(st.model_dump_json(indent=1), encoding="utf-8")
    error = ""
    try:
        async with asyncio.timeout(timeout):
            await game.start()
        for text in actions:
            if st.ending:
                break
            before, now = st.minutes, time.monotonic()
            calls = sum(x["calls"] for x in st.usage.values())
            async with asyncio.timeout(timeout):
                inp = {"input": text}
                while True:
                    interrupted = False
                    async for mode, chunk in app.astream(inp, thread, stream_mode=["custom", "updates"]):
                        if mode == "custom":
                            await emit(chunk)
                        elif "__interrupt__" in chunk:
                            interrupted = True
                    if not interrupted:
                        break
                    inp = Command(resume="drop")  # eval never silently spends a player's luck or pushes their risk
            rounds.append({"input": text, "seconds": round(time.monotonic() - now, 2),
                           "calls": sum(x["calls"] for x in st.usage.values()) - calls,
                           "minutes": st.minutes - before, "location": st.location,
                           "clues": list(st.clues), "world_facts": len(st.world)})
    except Exception as exc:
        error = type(exc).__name__
    finally:
        await game.close()
        await cast.close()
    report = {"module": m.id, "completed": len(rounds), "requested": len(actions), "error": error,
              "rounds": rounds, "events": entries, "state": st.model_dump(mode="json"),
              "review": ["是否替玩家行动或感受？", "闲聊是否总被拉回主线？", "约定和物品是否前后相符？",
                         "有没有透露未获知的线索或人物秘密？", "人物回应是否重复、是否有自己的生活？"]}
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
