# 地下室走廊

[返回《失落的对称》](../../README.md) · 主持资料，含剧透

```yaml
id: cora_basement_hall
name: 地下室走廊
read_aloud: 楼梯通向一条狭小的走廊，尽头一扇门。嗡嗡声在这里愈发强烈，好像就是从门后发出的。
keeper: 满是尘土的楼梯井上有脚印（侦查）。
features:
- 满是尘土的楼梯
- 通往主地下室的门
art: cora_basement_hall
within: cora_house
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_footprints
  change: 尘土覆盖的台阶上清楚地印着一串上下往来的脚印
  'on': cora_dusty_stairs
- when: flag:sign_fs_blue_under_door_1
  change: 门缝底下那一线淡蓝色的光没有了，走廊暗了下来。
views:
- key: cora_dusty_stairs
  label: 积满尘土的楼梯
  features:
  - 满是尘土的楼梯
  prompt: 通往地下室的窄木楼梯，台阶上积着厚厚的灰尘，下面是一条狭小的走廊和一扇关着的门，门缝里透出一点淡蓝色的光
```
