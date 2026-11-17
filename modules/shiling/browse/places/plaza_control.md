# 中控室

[返回《失灵》](../../README.md) · 主持资料，含剧透

```yaml
id: plaza_control
name: 中控室
read_aloud: 一间上锁的小房间，一台电脑控制着广场四块大屏，机柜的指示灯一闪一闪。
keeper: 电脑要密码：计算机使用（困难）检定并花 1D4 小时破解，奥斯丁知道密码。这台电脑正常不能联网，现在已被皮特动了手脚。
features:
- 控制电脑
- 机柜与线路
art: plaza_control
within: central_plaza
outdoors: false
ground: building
looks:
- when: at:peter:plaza_control
  change: 控制电脑屏幕亮着，开了好几个代码窗口，机柜门敞开，桌上摊着一只打开的工具包
views:
- key: plaza_control_pc
  label: 中控电脑与机柜
  features:
  - 控制电脑
  - 机柜与线路
  prompt: 狭小的中控室里，一台电脑屏幕停在密码输入框，旁边的机柜指示灯一闪一闪，后面是捆扎好的线缆。
```
