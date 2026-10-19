# 书房的旧书架：找到书架上的空档以后，右栏多一页「旧书架」；在书房输入 /理书架，
# 照道格拉斯目录卡上的三句话把剩下的四本书排回去。排好了托马斯会高兴，不排也不耽误查案。

BOOKS = ["红字", "白鲸", "瓦尔登湖", "草叶集"]  # the right order, left to right
SPINES = ["红字", "白鲸", "瓦尔", "草叶"]
CARD = "目录卡上的字被水洇了，只认得出三句：红字在白鲸左边；草叶集在最右；瓦尔登湖紧挨着草叶集。"


def shelf(placed):
    slots = []
    for i in range(4):
        slots.append("[" + SPINES[i] + "]" if i < placed else "[    ]")
    return "+" + "-" * 27 + "+\n|" + " ".join(slots) + "|\n+" + "-" * 27 + "+"


def panel(state):
    placed = len(state.get("placed", []))
    rows = [
        {"kind": "art", "art": shelf(placed)},
        {"kind": "text", "text": "架上空着好几格，像缺了牙。"},
        {"kind": "bar", "text": "理书", "value": placed, "max": 4, "color": "accent"},
    ]
    if state.get("done"):
        rows.append({"kind": "text", "text": "照老规矩理好了。", "color": "success"})
    else:
        rows.append({"kind": "text", "text": "在书房输入 /理书架，把剩下的书排回去。", "color": "dim"})
    return [{"key": "shelf", "title": "旧书架", "rows": rows}]


def ask(state, slot, text):
    left = [b for b in BOOKS if b not in state.get("placed", [])]
    return {"kind": "prompt", "key": "slot", "title": "理书架", "style": "choice",
            "text": CARD + "\n" + text + "第 " + str(slot + 1) + " 格放哪本？", "options": left, "timeout": 300}


def say(title, text):
    return {"kind": "show", "shape": "inline", "title": title, "frames": [{"text": text, "ms": 1500}]}


def handle(event, ctx, api):
    state = dict(ctx["state"])
    hook = event.get("hook")
    if hook == "clue":
        if event.get("clue") != "c_shelf_gap" or state.get("seen"):
            return {"state": state, "effects": []}
        state["seen"] = True
        state["placed"] = []
        return {"state": state, "effects": [], "panels": panel(state)}
    if hook == "command":
        if not state.get("seen"):
            return {"state": state, "effects": [say("理书架", "书架上还看不出什么名堂，先四处看看吧。")]}
        if state.get("done"):
            return {"state": state, "effects": [say("理书架", "书架已经照老规矩理好了。")]}
        if ctx["location"] != "shufang":
            return {"state": state, "effects": [say("理书架", "得回到道格拉斯的书房才能理。")]}
        return {"state": state, "effects": [ask(state, len(state.get("placed", [])), "")]}
    if hook == "answer" and event.get("prompt") == "slot" and not state.get("done"):
        if event.get("by") != "player":
            return {"state": state, "effects": [], "panels": panel(state)}
        placed = list(state.get("placed", []))
        slot = len(placed)
        if event.get("value") != BOOKS[slot]:
            state["tries"] = state.get("tries", 0) + 1
            return {"state": state, "effects": [ask(state, slot, "「" + str(event.get("value")) + "」放这儿不对劲，再想想。")]}
        placed.append(BOOKS[slot])
        if len(placed) == 3:
            placed.append(BOOKS[3])
        state["placed"] = placed
        if len(placed) < 4:
            return {"state": state, "effects": [ask(state, len(placed), "放好了。")], "panels": panel(state)}
        state["done"] = True
        frames = [
            {"art": shelf(2), "ms": 700},
            {"art": shelf(3), "ms": 700},
            {"art": shelf(4), "text": "最后一本推进去，严丝合缝。", "ms": 1500, "color": "success"},
        ]
        return {"state": state, "status": "旧书架：理好了", "panels": panel(state), "effects": [
            {"kind": "show", "shape": "inline", "title": "理书架", "frames": frames},
            {"kind": "note", "text": "书架照道格拉斯的老规矩理好了"},
            {"kind": "attitude", "npc": "thomas", "amount": 1, "text": "调查员替叔叔把书架理好了"},
            {"kind": "hint", "text": "调查员照道格拉斯的老规矩理好了书架，托马斯看着书架出了一会儿神"},
        ]}
    return {"state": state, "effects": []}
