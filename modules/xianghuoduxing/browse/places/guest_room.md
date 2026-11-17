# 客房

[返回《向火独行》](../../README.md) · 主持资料，含剧透

```yaml
id: guest_room
name: 客房
read_aloud: 客房很冷，一张床、一个壁炉、一扇门。门没有锁闩。
keeper: '§154（D1 夜）：梦见炉火蔓延，醒来壁炉是冷的；受过伤回 1 点生命。§58：被脚步声吵醒，受过伤回 1 点生命。

  §157（D2 夜）：客房像牢房。睡下（§224）：又梦见壁炉里悬浮的、没有燃料的火焰，蓝黄红紫，火舌刷过皮肉→§26 醒来腹痛、空气芳香馥郁，当天技能检定带惩罚骰。不睡（§230）：门把手慢慢转动，见事件
  e_may_night。

  §52：彻夜不眠，日出是病态的橙色，梅在厨房，前门开开关关；体质检定，失败当天技能检定带一颗惩罚骰。

  §248：拉开门后可搬厨房椅子顶住房门安心小睡（§58）。

  '
features:
- 壁炉
- 床
- 房门
art: guest_room
within: ledbetter_house
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_powder
  change: 壁炉角落里多了顶针大小的一撮黑色粉末,带着一缕细烟
  'on': guest_fireplace
views:
- key: guest_fireplace
  label: 客房壁炉
  features:
  - 壁炉
  prompt: 冷清客房里的石砌壁炉,炉膛里是冰冷的灰烬,旁边是一张铺着洁净床单的床
```
