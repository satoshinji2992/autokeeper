# 地下层走廊

[返回《直到我们灰飞烟灭》](../../README.md) · 主持资料，含剧透

```yaml
id: lab_corridor
name: 地下层走廊
read_aloud: 地下层的走廊又潮又暗，灯泡嗡嗡作响，尽头是一扇沉重的铁门。
keeper: 铁门锁是焊死的，要求仔细观察或试图撬锁就能看出。强闯不行；用切割机可行，但艾伯哈德会赶来（设 lab_door_cutting）。
features:
- 沉重的铁门
art: lab_corridor
within: steelworks
weather:
- flicker
outdoors: false
ground: building
looks:
- when: at:players:lab_corridor & flag:lab_door_cutting
  change: 铁门前架着一台切割机，地上溅满火花和焊渣，门板上切出一道没切完的口子
- when: knows:players:clue:c_welded_lock
  change: 凑近看，锁孔和门缝被焊料封得严严实实，根本插不进钥匙
  'on': lab_iron_door
views:
- key: lab_iron_door
  label: 沉重的铁门
  features:
  - 沉重的铁门
  prompt: 潮湿昏暗的走廊尽头一扇厚重的铁门，门板上有锈迹，一把锁嵌在门上，头顶灯泡嗡嗡作响
```
