# 主地下室

[返回《失落的对称》](../../README.md) · 主持资料，含剧透

```yaml
id: cora_basement
name: 主地下室
read_aloud: 中等大小的房间，空气潮湿。所有架子和橱柜都被拉到一边，中间空出一片。整个房间被淡蓝色的光照亮。空地中央，詹姆斯赤裸的身体悬在半空，下面的地上是一个用血涂成的亵渎图案。旁边放着一本古老的书和一个桶。
keeper: 詹姆斯似乎在呼吸；唯一能看出他意识到处境的迹象，是他的眼神会聚焦到任何叫他名字的人身上，此外无法动弹。到达后不久守卫者下楼（事件 e_guardian）。杂乱的地下室可供躲藏（潜行奖励骰）。关闭大门的办法见守秘人须知。
features:
- 悬浮的詹姆斯
- 地上的血绘图案
- 古老的书
- 桶
- 拉到一边的架子和橱柜
art: cora_basement
within: cora_house
weather:
- flicker
outdoors: false
ground: building
looks:
- when: flag:gate_closed
  change: 蓝光熄灭，地下室只剩昏暗，空地上的人形瘫在血绘图案旁的地上
- when: knows:players:clue:c_chest_sigil
  change: 他胸口刻着一个血淋淋的图形，和地上的血绘图案一模一样
  'on': basement_floating_body
- when: knows:players:clue:c_bucket
  change: 桶里装着满满的暗红色血液
  'on': basement_bucket
views:
- key: basement_floating_body
  label: 悬在半空的人
  features:
  - 悬浮的詹姆斯
  prompt: 潮湿的地下室被淡蓝色光照亮，一个赤裸消瘦的年轻男子悬浮在半空，身体微微起伏像在呼吸，下方地面上是用血涂成的几何图案
- key: basement_blood_sigil
  label: 地上的血绘图案
  features:
  - 地上的血绘图案
  prompt: 地下室中间清空的水泥地面上，用暗红的血涂出一个巨大的几何图形，圆、交叉的基准线和锯齿形折线，四周的架子和橱柜被推到墙边，一切笼罩在淡蓝色的光里
- key: basement_old_book
  label: 图案旁的旧书
  features:
  - 古老的书
  prompt: 血绘图案旁边的地上放着一本古老厚重的书，皮面磨损，书页发黄卷边，被淡蓝色的光照着
- key: basement_bucket
  label: 图案旁的桶
  features:
  - 桶
  prompt: 血绘图案旁边放着一只旧木桶（或铁皮桶），桶沿上挂着暗红色的干涸痕迹
```
