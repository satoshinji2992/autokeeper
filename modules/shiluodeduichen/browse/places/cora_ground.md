# 一楼起居室与厨房

[返回《失落的对称》](../../README.md) · 主持资料，含剧透

```yaml
id: cora_ground
name: 一楼起居室与厨房
read_aloud: 屋里收拾得很整洁，但家具上蒙着一层薄灰。起居室里摆着沙发和圆桌，厨房在后面。
keeper: 起初没有可疑之处。四处看过一会儿后，侦查成功发现恶臭从通往二楼的楼梯传来。一侧有通往地下室的楼梯。
features:
- 起居室
- 厨房
- 冰箱
- 通往二楼的楼梯
- 通往地下室的楼梯
art: cora_ground
within: cora_house
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_spoiled_food
  change: 冰箱门打开，里面一块用保鲜膜包着的自制烘肉卷长满了霉斑
  'on': cora_icebox
views:
- key: cora_icebox
  label: 厨房里的冰箱
  features:
  - 冰箱
  prompt: 1920年代乡间小屋整洁的厨房，一台老式木制冰箱（冰柜）门关着，旁边台面上落着一层薄灰，果盘里的水果颜色发暗
```
