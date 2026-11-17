# 酒吧地窖

[返回《失灵》](../../README.md) · 主持资料，含剧透

```yaml
id: bar_cellar
name: 酒吧地窖
read_aloud: 储存饮料的地窖。中间是两人宽的通道，两侧的架子上摆满瓶瓶罐罐，临街的墙上有一个窄小的通风口，透进街上的声音。到处是频繁使用的痕迹。
keeper: '门没锁。向约瑟芬申请，她干脆地同意；被悖论发现进入也不阻止。但只要悖论发现特工进了地窖，就在特工进入后偷偷锁上门（事件：被困在地窖）。进入时个体腐化 +1。

  幻象“地窖”（阈值 25）：腐化<25 时地窖门只是一幅画着门的装饰画；掀开这幅画，它变成木门，SAN 0/1D3，腐化 +2。

  '
features:
- 两侧的架子
- 通风口
- 木门
art: bar_cellar
within: bar
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_peter_laptop
  change: 架子后面藏着一台手提电脑，接着一根数据线和一根网线。
  'on': cellar_shelves
views:
- key: cellar_shelves
  label: 地窖两侧的架子
  features:
  - 两侧的架子
  - 通风口
  prompt: 窄长的地窖，中间两人宽的通道，两侧架子上摆满瓶瓶罐罐和酒箱，临街墙高处一个窄小的通风口透进街上的声音和光。
```
