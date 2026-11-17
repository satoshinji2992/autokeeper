# 排水通道

[返回《鱼！鱼！鱼！》](../../README.md) · 主持资料，含剧透

```yaml
id: drain_tunnel
name: 排水通道
read_aloud: 排水通道在排洪箱涵里。最开始它看起来确实像一处废弃的排水设施，越往里走，就越不像现代工程能留下来的东西：下半部分是粗糙的石砌结构，石缝里长满白色的贝壳和某种已经硬化的海洋生物残骸，墙壁上没有现代排水系统该有的编号和检修标记。通道的尺寸也不寻常，有些地方高得出奇，有些地方又突然变得低矮。前方有好几处积水。
keeper: '调查时长 60 分钟。第一次进入时，调查员只会以为那些声音来自自己的脚步和水流回声；随着前进，回声没有随距离消失，反而始终跟在身后，越来越近；回头时，昏暗的手电光只能照亮一截湿润的石壁。拐过一处低矮的弯道后进入追逐（事件
  e_tunnel_chase）。

  追逐路线照原书 10 个点排列，调查员起始于点 4，深潜者起始于点 1；点 10 是一条狭窄的侧向裂隙，通向闸门，抵达即逃脱。D2 02:24 后通道被完全淹没。

  '
features:
- 石砌墙壁
- 石缝里的白色贝壳
- 积水区
- 低矮的弯道
art: drain_tunnel
aliases:
- 排水箱涵
- 地下排水通道
- 排水巷路
scene_root: true
weather:
- torch
outdoors: false
ground: building
views:
- key: tunnel_stone_wall
  label: 长满贝壳的石壁
  features:
  - 石砌墙壁
  - 石缝里的白色贝壳
  prompt: 手电光里一段粗糙的石砌墙壁，石缝里密密长满白色贝壳和硬化的海洋生物残骸，墙上没有任何编号或检修标记
- key: tunnel_low_bend
  label: 低矮的弯道和积水
  features:
  - 低矮的弯道
  - 积水区
  prompt: 通道顶突然压低，拐进一处低矮的弯道，地上一片平静的黑色积水，手电光照不到尽头
chase:
- id: tunnel_p1
  name: 黑暗深处的积水
  look: 远处原本平静的积水里接连响起扑通声
  place: drain_tunnel
- id: tunnel_p2
  name: 坍塌的条石堆
  look: 通道被一堆坍塌的条石和锈蚀的金属构件堵塞
  obstacle:
    kind: barrier
    what: 坍塌的条石和锈蚀金属
    skill: 攀爬
    methods:
    - id: climb
      label: 爬过碎石堆
      skill: 攀爬
  place: drain_tunnel
- id: tunnel_p3
  name: 通道中段
  look: 湿漉漉的石壁向两头延伸
  place: drain_tunnel
- id: tunnel_p4
  name: 低矮弯道后
  look: 刚拐过的低矮弯道，手电光照着脚下的积水
  place: drain_tunnel
- id: tunnel_p5
  name: 石砌通道
  look: 头顶忽然高得出奇，脚步声在里面打转
  place: drain_tunnel
- id: tunnel_p6
  name: 长满苔藓的条石
  look: 条石表面覆满灰绿色苔藓，被海水浸湿后异常湿滑
  obstacle:
    kind: barrier
    what: 湿滑的苔藓路面
    skill: 敏捷
    methods:
    - id: step
      label: 稳住脚步走过去
      skill: 敏捷
  place: drain_tunnel
- id: tunnel_p7
  name: 狭长的积水段
  look: 水没过脚踝，每一步都溅起水声
  place: drain_tunnel
- id: tunnel_p8
  name: 贝壳石壁
  look: 两侧石缝里挤满白色贝壳
  place: drain_tunnel
- id: tunnel_p9
  name: 腐臭的岔口
  look: 空气里弥漫着臭鸡蛋和腐烂海藻的气味，令人头晕恶心
  obstacle:
    kind: hazard
    what: 令人眩晕的气体
    skill: 体质
  place: drain_tunnel
- id: tunnel_p10
  name: 侧向的窄裂隙
  look: 一条狭窄的侧向裂隙，只够孩子侧身挤进去
  place: drain_gate
  exit: true
```
