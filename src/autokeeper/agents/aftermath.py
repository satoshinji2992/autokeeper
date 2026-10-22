"""The keeper's next arrangements when nobody in the party can act."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Aftercare(BaseModel):
    kind: Literal["first_aid", "medicine", "move", "wait", "wake", "finish"]
    who: str = Field("", description="实施急救、医学或移动的已登记 NPC id；等待、苏醒、收束留空")
    target: str = Field("", description="接受治疗、苏醒的调查员 id；移动时要搬运全体在场调查员填 players，不带人留空")
    to: str = Field("", description="移动目的地 id，其他步骤留空")
    minutes: int = Field(0, ge=0, le=10080, description="本步骤先经过的游戏分钟；医学至少60；急救为0，按轮结算")
    repeat: bool = Field(False, description="仅用于濒死急救：明确持续施救，失败后只要伤者活着就再试，直到稳定")
    reason: str = Field(description="行动依据与目的；说明施救意愿、运输方式或等待什么，不能只写抽象结果")


class Aftermath(BaseModel):
    reason: str = Field(description="结合现场、各人动机和可能到来的援助，说明下一段怎样处理；这是幕后安排，不是公开叙述")
    steps: list[Aftercare] = Field(min_length=1, description="依次执行的近期安排；未知结果会影响后续安排时，不预写很远")


AFTERMATH = """
【全员暂时不能行动：安排接下来发生的事】
你仍是本局全知、拥有最高权限的守秘人；这次只交安排，不写正文。考虑眼前所有人、动机、关系、危险、
交通与医疗资源，以及已约定或日程中即将到来的帮助。NPC 在场不等于愿意救人；全员倒下也不等于无人能救。
可以施救、送医、转移、等待苏醒，或根据实际结果收束。不要为了继续游戏无条件救活，也不要自动处决。
这次安排只使用已登记的人和地点；可以安排既有人物赶来，不能把不存在的救援者当成已经在场。
first_aid：在场、能行动的人实施；程序掷急救，成功暂时稳定濒死者，其他昏迷者恢复1点并苏醒。
濒死急救可明确 repeat=true 持续施救，每次失败后仍须结算该轮的体质，成功不是你预定的。
medicine：由实际在场的人治疗，至少一小时；濒死者必须先急救稳定。程序按人物数值掷医学，成功恢复1D3并唤醒。
move：who 移到 to，minutes 是实际路程；若确实搬运在场调查员，target=players 并在 reason 写清运输方式。
wait：经过 minutes，程序会在事件、援助、伤势变化或苏醒时停下，局势变了再安排，不凭空跳过危险。
wake：你判断昏迷持续足够时间后让 target 苏醒；不是治疗，不能唤醒仍濒死、死亡或0生命的重伤者。
finish：明确判断调查就此中断时才用，必须说明尚存的人为何不能恢复这次调查；不是判定他们死亡。
伤势稳定但不能自行苏醒，不应直接收束：先考虑医学和可到来的援助。未来的帮助必须来得及，不把赶路省略。
死亡、濒死、治疗成败与时间由程序结算。有人恢复行动就把决定权交还给玩家；不要替他们安排下一步调查。
"""
