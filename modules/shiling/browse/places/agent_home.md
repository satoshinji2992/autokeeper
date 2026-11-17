# 特工的家

[返回《失灵》](../../README.md) · 主持资料，含剧透

```yaml
id: agent_home
name: 特工的家
read_aloud: 楼下的客厅连着门廊，门上有猫眼。客厅一角立着书架，茶几上放着家里的座机电话。楼梯通往二楼的卧室，客厅一侧是书房的门。
keeper: 特工与丈夫阿尔伯特、6 岁女儿爱丽丝的家（原文只写“特工的家”，具体陈设为 AutoKeeper 补充）。它是被腐蚀的地点之一，是地图上黄印的一个转折点，原文未给环境腐化值。家人会被深夜出门吵醒；多次目睹特工的怪异举动会偷偷联络心理医生（附录C）。剧院中无明确去处时主动离开，会回到这里或亚当森家。书架上可能找到《纸牌大学（第一卷）》。
features:
- 门廊与猫眼
- 书架
- 客厅的电话
art: agent_home
scene_root: true
outdoors: false
ground: building
looks:
- when: at:kandi:agent_home
  change: 门廊外的路边停着一辆熄了引擎和车灯的深色轿车
views:
- key: home_door_peephole
  label: 门廊的门与猫眼
  features:
  - 门廊与猫眼
  prompt: 深夜，客厅通往门廊的木门紧闭，门上一枚小小的猫眼，门缝底下透进一线路灯的冷光，门边挂着外套和钥匙。
- key: home_living_phone
  label: 茶几上的座机
  features:
  - 客厅的电话
  prompt: 客厅茶几上一部家用座机电话，听筒搁在机座上，旁边有遥控器和几本杂志，台灯光线昏黄。
```
