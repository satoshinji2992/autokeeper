# 二楼卧室

[返回《失灵》](../../README.md) · 主持资料，含剧透

```yaml
id: home_bedroom
name: 二楼卧室
read_aloud: 二楼的卧室。丈夫阿尔伯特在床上平稳地呼吸，床头的电子钟亮着。门外的走廊尽头是女儿爱丽丝的房间，门缝里透出一点小夜灯的光。
keeper: '开场时特工在这里醒来，敲门声来自楼下。阿尔伯特和爱丽丝通常在这一层；家人会被深夜出门吵醒，可能看到特工对着空气比划；多次目睹会偷偷联络心理医生（附录C）。

  （AutoKeeper 补充：开场原文写特工在自己卧室醒来、敲门声在楼下，地点描述写卧室与走廊尽头的女儿房间；分出来以便区分家人在不在场、深夜出门是否吵醒家人。）'
features:
- 卧室床头
- 走廊尽头爱丽丝的房门
art: home_bedroom
within: agent_home
outdoors: false
ground: building
looks:
- when: flag:sign_fs_unconscious_notes_1
  change: 半开的衣柜里，常穿的外套之间多出一抹灰色粗布。
views:
- key: bedroom_bedside_clock
  label: 床头的电子钟
  features:
  - 卧室床头
  prompt: 黑暗卧室的床头柜上，一只电子钟亮着红色数字，旁边是台灯和一杯水，床上有人平稳地睡着。
```
