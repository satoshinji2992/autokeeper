# 礁石滩的大礁石

[返回《鲅鱼？鲅鱼！》](../../README.md) · 主持资料，含剧透

```yaml
id: reef_rock
name: 礁石滩的大礁石
read_aloud: 远海的礁石滩上遍布犬牙交错的礁石，海水呈墨蓝色，浪头拍在礁石上发出阵阵闷响。渔船停在一块面积相对大些的礁石旁，跨一步就能登上去喘口气。
keeper: '船到礁石滩后（at_reef）才能登上。晕过船的调查员在这里休息片刻，下一次晕船检定额外一个奖励骰（插件 bayu_sea 的 rest 动作）。

  调查脚下礁石：格外光滑，表面没有任何贝壳或藤壶附着（c_reef_smooth）；<侦查>在缝隙中找到半只手套（c_glove）；触碰海水会发觉水温极低（c_cold_water）。

  '
features:
- 脚下的礁石
- 礁石缝隙
- 礁石边的海水
art: reef_rock
scene_root: true
weather:
- fog
outdoors: true
ground: water
looks:
- when: knows:players:clue:c_glove
  change: 缝隙里卡着半只亮黄色塑胶手套，断口参差不齐，像被巨力扯开
  'on': reef_crevice
views:
- key: reef_smooth_rock
  label: 脚下的礁石
  features:
  - 脚下的礁石
  prompt: 犬牙交错的礁石间，墨蓝色海水拍上来；脚下的礁石格外光滑，表面没有一点贝壳或藤壶
- key: reef_crevice
  label: 礁石缝隙
  features:
  - 礁石缝隙
  prompt: 两块湿黑礁石之间的狭窄缝隙，积着海水和碎沫
```
