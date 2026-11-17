# 街区外的小树林

[返回《直到我们灰飞烟灭》](../../README.md) · 主持资料，含剧透

```yaml
id: weber_woods
name: 街区外的小树林
read_aloud: 光秃秃的桦树林，地上是湿透的落叶，林子边缘有一小块土被翻过又填平。
keeper: 老太太在这里发现韦伯被枪击的尸体，也想就近把他埋了。困难侦查可找到当时留下的弹壳。
features:
- 发现尸体的地方
- 韦伯的坟
art: weber_woods
aliases:
- 小树林
weather:
- fog
outdoors: true
ground: woods
looks:
- when: knows:players:clue:c_shell
  change: 拨开落叶，下面有一枚旧弹壳
  'on': weber_woods_spot
views:
- key: weber_woods_spot
  label: 发现尸体的地方
  features:
  - 发现尸体的地方
  prompt: 光秃秃的桦树之间，一片湿透的落叶，看不出什么特别
- key: weber_grave
  label: 韦伯的坟
  features:
  - 韦伯的坟
  prompt: 林子边缘一小块翻过又填平的土，没有墓碑，土面上有一圈发黑的焦痕
```
