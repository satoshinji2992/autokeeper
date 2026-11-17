# 地窖

[返回《向火独行》](../../README.md) · 主持资料，含剧透

```yaml
id: cellar
name: 地窖
read_aloud: 活板门下是阴冷的地窖，靠墙摞着一大堆旅行箱和公文包。
keeper: §114：看完后退回去，把床推回原位（§120）。
features:
- 旅行箱和公文包
art: cellar
within: ledbetter_house
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_luggage
  change: 许多箱子的提手上挂着行李标签,写着各不相同的名字
  'on': cellar_luggage
views:
- key: cellar_luggage
  label: 摞起的行李箱
  features:
  - 旅行箱和公文包
  prompt: 阴冷昏暗的地窖,靠墙摞着一大堆各种尺寸的旅行箱和公文包,落满灰尘
```
