# 地下实验室

[返回《直到我们灰飞烟灭》](../../README.md) · 主持资料，含剧透

```yaml
id: lab
name: 地下实验室
read_aloud: 门后是冶炼和锻造的设备，却没有连接任何供能装置；一座巨大的焚尸炉占据了一面墙，旁边立着两个柜子。
keeper: '终战地点。两个存放赤钢的柜子：一个放动物实验得到的赤钢；另一个放三个金发受害者的福尔马林头罐，和用他们（以及三个犹太受害者）的尸体烧炼的金属，贴着花体字姓名标签。

  '
features:
- 存放赤钢的柜子
- 焚尸炉
- 冶炼锻造设备
art: lab
within: steelworks
weather:
- embers
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_lab_heads
  change: 一个柜子里堆着银白色的金属；另一个柜子里是三个泡在福尔马林里的金发男人头颅，和一块块金属放在一起，各贴着花体字姓名标签
  'on': lab_cabinets
views:
- key: lab_cabinets
  label: 存放赤钢的柜子
  features:
  - 存放赤钢的柜子
  prompt: 焚尸炉旁并排立着两个关着门的金属柜子，柜门上贴着花体字的标签
- key: lab_furnace
  label: 巨大的焚尸炉
  features:
  - 焚尸炉
  - 冶炼锻造设备
  prompt: 占据整面墙的巨大焚尸炉，炉门紧闭，旁边是冶炼和锻造设备，没有接任何电线或燃料管
```
