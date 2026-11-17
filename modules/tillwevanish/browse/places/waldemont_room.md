# 沃德蒙特的书房与卧室

[返回《直到我们灰飞烟灭》](../../README.md) · 主持资料，含剧透

```yaml
id: waldemont_room
name: 沃德蒙特的书房与卧室
read_aloud: 整洁的书房连着卧室，书架上是会计和工业年鉴，书桌收拾得一丝不苟。
keeper: 办公室和卧室都找不到他偷拍文件的任何线索。侦查可在书桌下方找到一把手枪，他会解释为防身。
features:
- 书桌下方
- 书架
art: waldemont_room
within: manor
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_desk_gun
  change: 桌面下方的暗处卡着一把手枪
  'on': waldemont_desk_under
views:
- key: waldemont_desk_under
  label: 书桌下方
  features:
  - 书桌下方
  prompt: 一丝不苟的书桌，桌下暗处是整齐的抽屉柜和桌腿，地毯平整
- key: waldemont_bookshelf
  label: 会计与工业年鉴的书架
  features:
  - 书架
  prompt: 书架上整齐排列着会计账册和工业年鉴，书脊按年份排好，没有一本放歪
```
