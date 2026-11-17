# 总工程师办公室

[返回《直到我们灰飞烟灭》](../../README.md) · 主持资料，含剧透

```yaml
id: segli_office
name: 总工程师办公室
read_aloud: 堆满图纸和计算尺的办公室，墙上钉着冲压模具的设计图，书柜里塞得满满当当。
keeper: 书柜里压着拉丁语打字转录本和塞格利手绘的图册。原文说转录本也可能在艾伯哈德卧室或塞格利卧室。
features:
- 书柜里的打字稿与图册
- 墙上的模具设计图
art: segli_office
within: steelworks
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_transcript
  change: 抽出一沓没有封面、翻得卷边的拉丁语打字稿，满页德语批注，下面压着一本手绘图册，画着装置和怪异符号，每张图都标了页码
  'on': segli_bookcase
views:
- key: segli_bookcase
  label: 塞满的书柜
  features:
  - 书柜里的打字稿与图册
  prompt: 塞得满满当当的书柜，工程手册、期刊和图纸卷筒挤在一起，下层压着成沓的纸
- key: segli_die_drawings
  label: 墙上的模具设计图
  features:
  - 墙上的模具设计图
  prompt: 墙上钉着冲压模具的设计图，标注精细的尺寸线中间画着繁复的花纹，旁边夹着计算尺和铅笔
```
