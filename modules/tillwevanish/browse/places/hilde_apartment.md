# 希尔德的公寓

[返回《直到我们灰飞烟灭》](../../README.md) · 主持资料，含剧透

```yaml
id: hilde_apartment
name: 希尔德的公寓
read_aloud: 公寓不大，收拾得很雅致，卧室里一张大床，床头柜上一盏黄铜台灯。
keeper: 床底一块被加尔文撬开的地板、沃德蒙特送的台灯空灯座，都能放巴掌大的薄物。极难侦查或花费足够多的时间可发现，里面没有任何东西。
features:
- 床底的地板
- 黄铜台灯
art: hilde_apartment
aliases:
- 莱费尔夫人的公寓
- 情人的公寓
scene_root: true
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_floor_hide
  change: 一块地板被撬开过，掀起来下面是个巴掌大的扁平空洞，空无一物
  'on': hilde_bed_floor
- when: knows:players:clue:c_lamp_hide
  change: 灯座底盖拧下后是空心的，里面能放巴掌大的薄物，什么也没有
  'on': hilde_brass_lamp
views:
- key: hilde_bed_floor
  label: 床底的地板
  features:
  - 床底的地板
  prompt: 大床下的暗处是拼接整齐的木地板，积着薄灰，床单垂下来遮住一半
- key: hilde_brass_lamp
  label: 床头的黄铜台灯
  features:
  - 黄铜台灯
  prompt: 床头柜上一盏擦得锃亮的黄铜台灯，灯座又圆又厚，旁边放着手帕和香水瓶
```
