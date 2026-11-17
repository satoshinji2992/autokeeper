# “私用”房间

[返回《向火独行》](../../README.md) · 主持资料，含剧透

```yaml
id: private_room
name: “私用”房间
read_aloud: 房间里有书架和文件柜，一角堆着食品，桌上放着一台电报机。书架上分门别类：数学科学、文史、文学、通俗小说，还有一摞破烂的《幻丽诡谭》。
keeper: '§62：文特斯说牌子只是防集会时有人随便进出，书其实属于全村。§68：一个下午只够读一类书，选一条（本地历史/节日/科学/怪诞小说）。§99：下午流逝，文特斯看窗外起身，信用评级检定：成功（§111）再留半小时，“没有借书卡不要带走书”，可做侦查（§118）；失败（§105）他说天黑前有差事，请明天下午再来，一同离开。

  §133（D3 潜入）：屋里昏暗；侦查（已检查过书架的给一颗奖励骰），成功见 c_secret_shelf，失败只找到书间夹着的地图（§140）。

  '
features:
- 文史书架
- 阿尼奥洛夫斯基博士手稿
- 天文书
- 《幻丽诡谭》
- 墙上黏着的书
- 靠砖窗的书架
- 书间夹着的地图
- 电报机
art: private_room
within: village_hall
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_manuscript
  change: 手稿读到末页戛然中断,最后一页的句子只写了一半
  'on': room_manuscript
- when: knows:players:clue:c_secret_shelf
  change: 一只书套被向左拉开,一段书架向外敞开,后面露出黑暗的缝隙
  'on': room_shelf_window
views:
- key: room_manuscript
  label: 手写的手稿
  features:
  - 阿尼奥洛夫斯基博士手稿
  prompt: 桌上摊着一盒泛黄的手写稿纸,字迹密密麻麻,纸边卷曲发脆
- key: room_shelf_window
  label: 靠砖窗的书架
  features:
  - 靠砖窗的书架
  prompt: 昏暗房间里一排挨着北墙的高书架,书脊整齐,有些书套看着像是粘在架子上的
```
