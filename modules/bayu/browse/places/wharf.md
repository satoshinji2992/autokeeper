# 芝罘湾渔人码头

[返回《鲅鱼？鲅鱼！》](../../README.md) · 主持资料，含剧透

```yaml
id: wharf
name: 芝罘湾渔人码头
read_aloud: 一排排小型近海渔船并列停着，船身上大多写着“鲁-烟-渔-XXXXX”的编号，船名五花八门，“一帆风顺号”“海的女儿号”，甚至还有个“必胜客号”。入口处的铺子亮着小灯泡。
keeper: '集合地点。调查员互不相识的话，可在这里简单自我介绍、交流。

  打电话问船长到哪儿了：老张说他大约十多分钟后过来，让调查员先找到船，去船上坐坐，并提醒他们记得吃点东西填饱肚子。

  在泊位中通过<侦查>找到乘风破浪888号（c_find_boat）；或在码头等到 4:40 左右，老张到达并带他们上船（事件 e_captain）。

  船离开码头后，这里只是远去的灯火。

  '
features:
- 泊位上的渔船
- 码头入口的铺子
art: wharf
public: true
aliases:
- 渔人码头
- 码头
scene_root: true
weather:
- fog
outdoors: true
ground: water
looks:
- when: knows:players:clue:c_find_boat
  change: 一排船中认出一艘船身写着“乘风破浪888号”的陈旧小渔船
  'on': wharf_berth_boats
views:
- key: wharf_berth_boats
  label: 泊位上并排的渔船
  features:
  - 泊位上的渔船
  prompt: 拂晓前墨蓝色的天色下，昏黄路灯照着一排并排停靠的小型近海渔船，船身漆着“鲁-烟-渔”开头的编号和各式船名，“一帆风顺号”“海的女儿号”“必胜客号”，海浪拍着船身和堤岸
```
