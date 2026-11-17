# 寄宿公寓门前

[返回《失落的对称》](../../README.md) · 主持资料，含剧透

```yaml
id: dorm_yard
name: 寄宿公寓门前
read_aloud: 校园东侧一栋较新的寄宿公寓，砖墙上爬满常春藤，一楼的窗户一字排开。
keeper: 詹姆斯的房间在一楼。进门失败、准备离开时要求一次侦查，成功会注意到他房间的窗户微开（线索 c_window），旁边是常春藤覆盖的排水管。
features:
- 公寓正门
- 一楼的窗户
- 常春藤覆盖的排水管
art: dorm_yard
scene_root: true
outdoors: true
ground: street
looks:
- when: knows:players:clue:c_window
  change: 紧挨排水管的那扇窗户下沿留着一道窄缝，窗扇微微开着
  'on': dorm_ivy_window
views:
- key: dorm_ivy_window
  label: 常春藤旁的一楼窗户
  features:
  - 一楼的窗户
  - 常春藤覆盖的排水管
  prompt: 砖砌寄宿公寓的一楼外墙，一排木框上下推拉窗，其中一扇紧挨着一根爬满茂密常春藤的铸铁排水管
```
