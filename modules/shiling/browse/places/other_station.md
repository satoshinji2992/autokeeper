# 彼岸·光辉的站台

[返回《失灵》](../../README.md) · 主持资料，含剧透

```yaml
id: other_station
name: 彼岸·光辉的站台
read_aloud: 闹钟酒吧的位置上是一座富丽堂皇的车站，宽阔明亮的大门里人潮涌动，卖报纸和饮料的机械玩偶在人群中游走。铁轨一直连到天际线，一列火车停在站台上。站台上没有时刻表。大门旁摆着一张小木桌和一条凳子。
keeper: 想乘坐剧院，侍者会请特工出示邀请函，确认后引入剧院。大门旁坐着但丁（见 NPC）。
features:
- 站台上的列车
- 大门旁的小木桌
- 机械玩偶
art: other_station
within: other_side
outdoors: false
ground: building
looks:
- when: at:dante:other_station
  change: 大门旁的小木桌上摆着一台吐着长纸带的蒸汽时代机械，凳子边靠着一根拐杖
views:
- key: station_small_table
  label: 大门旁的小木桌
  features:
  - 大门旁的小木桌
  prompt: 富丽堂皇的车站大门旁，一张小木桌和一条凳子，凳子上坐着一位穿袖口磨损红袍、双手杵着拐杖的瘦老人，桌上摆着一台复古的蒸汽装置。
- key: station_platform_train
  label: 站台上的列车
  features:
  - 站台上的列车
  - 机械玩偶
  prompt: 明亮的站台上停着一列维多利亚式火车，铁轨连到天际线，卖报纸和饮料的机械玩偶在人潮中游走，站台上找不到时刻表。
```
