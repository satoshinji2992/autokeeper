# 约兰迁移后的坟墓

[返回《直到我们灰飞烟灭》](../../README.md) · 主持资料，含剧透

```yaml
id: jolan_grave
name: 约兰迁移后的坟墓
read_aloud: 偏僻墓园角落里一座不起眼的墓，墓碑上的名字被青苔盖了一半。
keeper: 被挖开的坟墓和几乎是空的骨灰罐；侦查成功或要求仔细检查可发现一小片断裂的红色延长甲。地点由麦克斯纳告知。
features:
- 被挖开的坟墓
- 坟土里
art: jolan_grave
aliases:
- 约兰的墓
scene_root: true
weather:
- rain
outdoors: true
ground: graveyard
looks:
- when: knows:players:clue:c_nail
  change: 翻开的坟土里有一小片断掉的红色延长甲
  'on': jolan_open_grave
views:
- key: jolan_open_grave
  label: 被挖开的坟
  features:
  - 被挖开的坟墓
  - 坟土里
  prompt: 青苔半掩的墓碑前，坟被挖开，湿土堆在一边，坑底一只敞着盖的骨灰罐几乎是空的
```
