# 学校后的壁凹

[返回《向火独行》](../../README.md) · 主持资料，含剧透

```yaml
id: school_alcove
name: 学校后的壁凹
read_aloud: 村学校后面，悬崖顶上一处壁凹，草地一直伸到崖边。
keeper: '§169/§175/§182：阿博加斯特在这里讲“控制的仪式”，并愿意教咒语。学咒时天色已晚，云挡住了星星，“现在没有用”。拒绝的话，他憎恨地说“记得我曾经给过你触碰永恒的机会”，往东盘山路离去；调查员回梅家路上听到模糊、虚弱、绝望的男人叫声（§182）。

  刺杀见 e_assassination。§70（D3 早上）：回到这里只看见凝固发黏的斑块和草丛里熏黑的线条。

  '
features:
- 草地上的黑斑
- 崖边
art: school_alcove
aliases:
- 村学校
- 学校
scene_root: true
outdoors: true
ground: building
looks:
- when: knows:players:clue:c_school_scorch
  change: 草地上有凝固发黏的暗色斑块,和草丛里一道道熏黑的线条
  'on': alcove_scorch
views:
- key: alcove_scorch
  label: 草地上的黑斑
  features:
  - 草地上的黑斑
  prompt: 悬崖顶的壁凹,草地一直铺到崖边,边缘是夜晚般的黑暗
```
