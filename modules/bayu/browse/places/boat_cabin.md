# 驾驶室

[返回《鲅鱼？鲅鱼！》](../../README.md) · 主持资料，含剧透

```yaml
id: boat_cabin
name: 驾驶室
read_aloud: 驾驶室不大，仅容两三个人，充斥着柴油味和烟味。正面一扇视野开阔的玻璃窗，窗下是舵盘、油门推杆和一排排仪表。最引人注目的是一台老式短波电台，旁边挂着一个手持通话器。窗沿上放着一个有些发黑的木雕小像，被磨得油光发亮。
keeper: '短波电台：<电子学>、<知识>或<物理>成功了解通讯常识（c_radio）；判定没过，问老张也能得到解答。

  木雕小像：<侦查>或<艺术/手艺(雕刻)>（c_statue）；问老张他说祖传的“海神”（c_statue_zhang）。逃生时可把小像扔下海，令海和尚迟疑一轮（仅一次）。

  窗户角落：<侦查>见褪色黄纸符（c_talisman）。

  逃生时在这里用<驾驶>（或相关知识技能）启动引擎，推油门即可。

  '
features:
- 短波电台
- 木雕小像
- 窗户角落
- 舵盘和油门推杆
art: boat_cabin
within: boat
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_statue
  change: 看清小像雕的是一个盘坐在底座上、面目模糊的光头，不是龙王或妈祖
  'on': cabin_statue
- when: knows:players:clue:c_talisman
  change: 玻璃接缝处用透明胶带贴着一张小小的、已经褪色的黄纸符
  'on': cabin_window_corner
- when: flag:sign_fs_radio_static_1
  change: 电台面板上的一盏小红灯微微亮着。
views:
- key: cabin_radio
  label: 老式短波电台
  features:
  - 短波电台
  prompt: 驾驶室仪表旁一台老式短波电台，旋钮和指示灯泛旧，旁边挂着一个手持通话器，空气里有柴油和烟味
- key: cabin_statue
  label: 窗沿上的木雕小像
  features:
  - 木雕小像
  prompt: 玻璃窗沿上放着一个有些发黑的木雕小像，被摸得油光发亮，看不清雕的是哪路神佛
- key: cabin_window_corner
  label: 驾驶室窗户角落
  features:
  - 窗户角落
  prompt: 驾驶室正面玻璃窗的角落，窗框接缝处积着灰和盐渍，窗外是开阔的海面
- key: cabin_helm
  label: 舵盘和油门推杆
  features:
  - 舵盘和油门推杆
  prompt: 玻璃窗下的舵盘、一根油门推杆和一排排仪表，把手磨得发亮
```
