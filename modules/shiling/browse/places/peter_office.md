# 皮特的办公室

[返回《失灵》](../../README.md) · 主持资料，含剧透

```yaml
id: peter_office
name: 皮特的办公室
read_aloud: 写字楼的一角，一面落地窗对着街景。靠窗的磁带架倒了，磁带散落一地。办公桌空荡得反常。
keeper: '奥斯丁以私人空间为由婉拒特工进入；特工亮出警方等官方身份，他立刻同意，难掩担忧。据奥斯丁说，皮特的电脑不翼而飞。环境腐化 3。

  从这里进入剧院（阈值 30）：落地窗变成木质推拉门，推开直接进入剧院走廊。

  '
features:
- 办公桌抽屉
- 散落的磁带
- 落地窗
art: peter_office
within: horn_media
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_drawer_photos_b
  change: 拉开的抽屉里露出一沓照片，其中几张是夜里隔着窗户远远偷拍的、噪点很重，一张角落拍到了一块路牌。
  'on': peter_empty_desk
views:
- key: peter_empty_desk
  label: 空荡的办公桌
  features:
  - 办公桌抽屉
  prompt: 办公桌上空空荡荡，只剩显示器底座留下的印子和一截空的电源线，抽屉合着。
- key: peter_fallen_tapes
  label: 倒下的磁带架
  features:
  - 散落的磁带
  - 落地窗
  prompt: 落地窗边一只磁带架翻倒在地，上百盒磁带散落一地，窗外是白天的街景。
```
