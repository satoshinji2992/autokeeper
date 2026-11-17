# 银行

[返回《直到我们灰飞烟灭》](../../README.md) · 主持资料，含剧透

```yaml
id: bank
name: 银行
read_aloud: 大理石柜台后面的职员抬头看见你的制服，脸色一下子白了。
keeper: 艾伯哈德的保险柜在这里。用偷来的钥匙打开，或穿着制服拿着证件以国家安全为由恐吓职员打开（设 safe_opened；恐吓则同时设 safe_forced）。
features:
- 保险柜库房
art: bank
public: true
aliases:
- 保险柜
scene_root: true
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_vault
  change: 打开的柜格里放着一本拉丁文古书和一把瓦尔特 PPK，握柄花纹带着明显的手工刻痕
  'on': bank_vault
views:
- key: bank_vault
  label: 保险柜库房
  features:
  - 保险柜库房
  prompt: 厚重的库房门后一面墙的黄铜编号保险柜小门，中间一张铺绒布的桌子
```
