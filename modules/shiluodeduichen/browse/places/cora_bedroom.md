# 二楼卧室

[返回《失落的对称》](../../README.md) · 主持资料，含剧透

```yaml
id: cora_bedroom
name: 二楼卧室
read_aloud: 味道令人难以忍受。苍蝇在床上盘旋，床上躺着一个从头到脚盖着毯子的人形，毯子好几处被体液和血浸透，渗出的痕迹古怪地左右对称。
keeper: 科拉·韦布高度腐败的尸体。揭开毯子见线索 c_corpse（理智 0/1D4）。
features:
- 床上盖着毯子的尸体
art: cora_bedroom
within: cora_house
outdoors: false
ground: building
looks:
- when: flag:gate_closed
  change: 床上空了，毯子掀落在地，只剩浸透的污渍
- when: knows:players:clue:c_corpse
  change: 毯子被掀开，露出一位高度腐败的老妇人遗体，身上布满左右对称排列的深切口
  'on': cora_blanket_bed
- when: flag:sign_fs_blanket_shifted_1
  change: 床上的人形不见了，毯子半拖在地上，床边到门口的地板上多了一道污迹。
views:
- key: cora_blanket_bed
  label: 毯子下的人形
  features:
  - 床上盖着毯子的尸体
  prompt: 斜顶低矮的小卧室，苍蝇在一张旧床上方盘旋，床上躺着一个从头到脚盖着毯子的人形，毯子好几处被深色液体和血浸透，渗出的污痕左右对称
```
