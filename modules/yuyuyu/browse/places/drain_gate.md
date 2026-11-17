# 闸门口

[返回《鱼！鱼！鱼！》](../../README.md) · 主持资料，含剧透

```yaml
id: drain_gate
name: 闸门口
read_aloud: 侧身挤过那道狭窄的裂隙，身后的追赶声渐渐弱了下去。通道更深处，一道旧闸门横在黑暗里，海水的气味从它后面涌过来。闸门口坐着一个穿浅色短袖的小小身影。
keeper: '调查时长与通道合计 60 分钟。阮施宁在这里（事件 e_ruan_leaves）。

  仔细检查闸门可进行侦查检定：旧闸板没有完全脱离结构，一侧仍卡在轨道里，形成一道仅容一人勉强通过的缝隙，海水正从这里渗入；解决问题要把缝隙彻底封死。

  复位旧闸板：利用施工现场留下的工具让闸板重新卡入轨道。用撬棍强行清理过力量检定；用工具小心拆除障碍物过敏捷检定。成功则清出足够空间，闸板能够移动；失败则不仅没有进展，还可能惊动更深处的深潜者。

  临时封堵：无法复位时，可用工地的沙袋和水泥袋做简易结构。过一次智力检定，成功则找到一种虽简陋但足够稳定的方法，坚持到第二天，等待台风后施工队浇筑水泥。这无法永久解决问题，但足以阻止下一次涨潮的污染。

  每轮失败尝试额外损失 1 小时；可用“超越人体极限”多人协作检定，闸门 SIZ 120；可选每三次失败暗骰 1D3，结果为 1 遭遇 1 只巡逻深潜者。封死后设 gate_sealed。D2 02:24
  后此处被完全淹没。

  '
features:
- 旧闸门
- 闸板与轨道的缝隙
- 阮施宁坐过的地方
art: drain_gate
aliases:
- 闸门
- 旧闸门
- 排水闸门
within: drain_tunnel
weather:
- torch
outdoors: false
ground: building
looks:
- when: flag:gate_sealed
  change: 闸板的缝隙被推回轨道或用沙袋、水泥袋层层堵死，渗水停了
- when: knows:players:clue:c_gate_gap
  change: 看清闸板一侧还卡在轨道里，另一侧滑脱，留出一道仅容一人的缝隙，海水正从缝里渗进来
  'on': gate_old_sluice
- when: knows:players:clue:c_ruan_note
  change: 石台上留着几张湿透的稀有小浣熊卡片，和一张字迹歪歪扭扭的纸条
  'on': gate_ruan_seat
views:
- key: gate_old_sluice
  label: 旧闸门
  features:
  - 旧闸门
  - 闸板与轨道的缝隙
  prompt: 黑暗里横着一道锈蚀的旧闸门，闸板歪斜，周围石壁湿漉漉的，海腥味很重
- key: gate_ruan_seat
  label: 她坐过的地方
  features:
  - 阮施宁坐过的地方
  prompt: 闸门前一块湿漉漉的石台，地面积着薄薄一层海水
```
