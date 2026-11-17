# 高档餐厅单间

[返回《直到我们灰飞烟灭》](../../README.md) · 主持资料，含剧透

```yaml
id: restaurant
name: 高档餐厅单间
read_aloud: 埃森最好的餐厅，侍者领你穿过铺着白桌布的大堂，推开一间挂着丝绒帘子的单间。
keeper: 艾伯哈德用女人的名字订下单间（e_dinner）。
features:
- 丝绒帘子后的餐桌
art: restaurant
aliases:
- 餐厅
scene_root: true
weather:
- flicker
outdoors: false
ground: building
looks:
- when: at:eberhard:restaurant
  change: 单间桌上摆好两副餐具，点着烛台，冰桶里插着一瓶香槟
views:
- key: restaurant_private_table
  label: 丝绒帘子后的餐桌
  features:
  - 丝绒帘子后的餐桌
  prompt: 丝绒帘子后的小单间，白桌布上摆着银餐具、烛台和两只高脚杯，一瓶开好的红酒，桌上一张手写的预订卡
```
