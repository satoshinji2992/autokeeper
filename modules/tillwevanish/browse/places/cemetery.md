# 埃森公墓

[返回《直到我们灰飞烟灭》](../../README.md) · 主持资料，含剧透

```yaml
id: cemetery
name: 埃森公墓
read_aloud: 雨中的公墓，黑伞围着新挖的墓穴，一口沉重的橡木棺材停在穴边，花圈被雨打得耷拉下来。
keeper: '开场地点。葬礼进行中，沃德蒙特主持；洛特道别时棺材会自燃（e_coffin_fire）。火熄后可检查残骸，原文未写检定。

  棺材是阿克塞尔的：守灵夜艾伯哈德把约兰的骨灰撒了进去并施放熔铸之血，棺里留下一把刻着熔铸之血咒文的匕首。

  '
features:
- 烧过的棺材残骸
- 棺底的骨灰
- 棺里的匕首
art: cemetery
public: true
aliases:
- 墓园
- 葬礼现场
scene_root: true
weather:
- rain
outdoors: true
ground: graveyard
looks:
- when: knows:players:clue:c_coffin_bones
  change: 骨头断面和关节处夹着金属光泽，摸上去冰冷坚硬，像金属
  'on': cemetery_coffin_wreck
- when: knows:players:clue:c_dagger
  change: 灰里露出一把做工精致的匕首，柄上刻着和配枪握柄一面相近的花纹，刻痕明显是手工雕的
  'on': cemetery_coffin_ash_bed
views:
- key: cemetery_coffin_wreck
  label: 烧过的棺材残骸
  features:
  - 烧过的棺材残骸
  - 棺底的骨灰
  prompt: 雨里冒着细烟的焦黑橡木棺材残骸，棺板烧塌、边缘炭化，棺内是一副烧得干干净净的骨架，骨架下面和棺底积着厚厚一层灰白的骨灰，雨水打在灰上溅出小坑，没有油迹
- key: cemetery_coffin_ash_bed
  label: 棺底的灰堆
  features:
  - 棺里的匕首
  prompt: 焦黑棺板之间堆着湿透的灰白骨灰，碎炭和烧弯的铜钉散在灰里
```
