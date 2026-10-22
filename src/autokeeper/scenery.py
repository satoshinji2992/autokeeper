"""What sort of place a place is, from its name and its description: an office, a study, a cellar,
a shop, a bedroom; a street, a graveyard, a church, a station, water, woods, hills, fields. An
indoor place keeps the rain and snow of the module's outside pictures off its own.

Places, people, things and creatures with no picture of their own get a photo or a drawing
(pictures.py), never one painted by program: the user, 2026-10-21, after seeing a 「雪山上的血肉
墓碑」 come out as a green churchyard — shapes put together by keyword cannot draw what a module
says, and look the same in every module."""

from __future__ import annotations

KINDS: list[tuple[str, tuple[str, ...]]] = [
    ("cellar", ("地窖", "地下室", "墓室", "隧道", "地道", "下水道", "洞")),
    ("study", ("书房", "图书馆", "藏书", "档案", "阅览")),
    ("office", ("警察局", "局", "报社", "社", "邮局", "银行", "办公", "事务所", "诊所", "医院", "法庭", "市政")),
    ("shop", ("店", "铺", "酒吧", "餐馆", "饭馆", "牛排馆", "咖啡", "旅馆", "旅店", "发廊", "车行", "杂货")),
    ("bedroom", ("卧室", "客房", "出租屋", "房间", "阁楼", "病房")),
    ("glasshouse", ("温室", "天文台", "观测")),
    ("graveyard", ("墓", "坟", "陵", "公墓")),
    ("church", ("教堂", "会堂", "礼拜", "修道")),
    ("station", ("车站", "站台", "火车", "铁路")),
    ("water", ("河", "湖", "江", "港", "码头", "海", "溪", "湾", "渡口")),
    ("woods", ("林", "森", "树")),
    ("hills", ("山", "岭", "坡", "谷", "崖")),
    ("fields", ("农场", "田", "牧场", "庄园", "麦")),
    ("street", ("街", "镇", "广场", "路", "巷", "桥", "市场", "中心")),
    ("house", ("家", "宅", "屋", "房", "门廊", "院")),
]
INDOOR = {"cellar", "study", "office", "shop", "bedroom"}


def kind_of(name: str, description: str = "") -> str:
    """What sort of place this is: by its name first, then by what is said of it."""
    for text in (name, description):
        for kind, words in KINDS:
            if any(w in text for w in words):
                return kind
    return "street"
