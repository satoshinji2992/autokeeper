# 宅邸娱乐室

[返回《失灵》](../../README.md) · 主持资料，含剧透

```yaml
id: mansion_playroom
name: 宅邸娱乐室
read_aloud: 三楼的娱乐室，弥漫着血、精液和汗的腥臊味。正中一张标准台球桌，几把靠椅，台球杆靠在角落。门把手上挂着一条沾血的鞭子，原本放球杆的架子上挂满奇形怪状、刑具般的金属制品和粗细不一的绑带。
keeper: 吉姆和约瑟芬举行“爱之仪式”的地方。午夜时分特工会在这里撞见正在举行仪式的吉姆和约瑟芬，转入剧院事件“通往愉悦之路”。
features:
- 台球桌
- 门把手上的鞭子
- 挂满刑具的架子
art: mansion_playroom
within: greenwood_mansion
outdoors: false
ground: building
looks:
- when: at:jim:mansion_playroom
  change: 娱乐室灯火通明，门把手上的鞭子不见了，台球桌上散着绑带和金属器具，桌面的血迹还是湿的
- when: knows:players:clue:c_playroom_table
  change: 血迹一组一组排开，每一组都构成同一个图案。
  'on': playroom_billiard
views:
- key: playroom_billiard
  label: 斑驳的台球桌
  features:
  - 台球桌
  prompt: 标准大小的台球桌，绿色台呢上布满斑驳的褐色血迹，有些还很新鲜，几把靠椅围在旁边。
- key: playroom_whip_rack
  label: 鞭子与刑具架
  features:
  - 门把手上的鞭子
  - 挂满刑具的架子
  prompt: 门把手上挂着一条沾血的鞭子；原本放球杆的架子上挂满奇形怪状、像刑具一样的金属制品和粗细不一的绑带，几根台球杆靠在角落。
```
