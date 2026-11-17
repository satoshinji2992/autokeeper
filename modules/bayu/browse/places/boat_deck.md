# 船尾甲板

[返回《鲅鱼？鲅鱼！》](../../README.md) · 主持资料，含剧透

```yaml
id: boat_deck
name: 船尾甲板
read_aloud: 船尾的开放式甲板上整齐地固定着几排插鱼竿的卡座，摆好了钓鱼用的小马扎；旁边有活水鱼舱和几个装满冰块的泡沫箱。船舷边细心地绑了一圈防撞的旧轮胎。角落里摞着一个卡式炉和一个不锈钢锅，旁边放着桶装矿泉水、姜片和简单的调味料。
keeper: '活动区域。卡式炉和锅：调查员能判断是野外做饭的工具；问老张，他得意地说钓上来的第一条鲅鱼要现场片几片煮汤。

  船舷：<侦查>见旧爪形划痕（c_claw_marks）；问老张，他脸色微微一变，摆摆手搪塞。

  近海时这里是看日出、钓鱼、煮鱼汤的地方；礁石滩时这里是撒网和逃生的主战场：网绳缠在两舷挂钩和船尾固定栓上。

  '
features:
- 卡式炉和锅
- 船舷
- 活水鱼舱
- 插鱼竿的卡座
- 日出时的海面
- 船下的海水
- 缠成一团的旧渔网
- 船舷挂钩与船尾固定栓
art: boat_deck
within: boat
outdoors: true
ground: water
looks:
- when: knows:players:clue:c_claw_marks
  change: 不显眼的角落里露出几道很深的旧划痕，呈爪形，像被什么用力抓握过
  'on': deck_gunwale
- when: knows:players:clue:c_mirage_forest
  change: 紫雾里远方海平线上短暂浮现一片扭曲的黑色剪影，像从海底升起的森林
  'on': deck_sunrise_sea
- when: knows:players:clue:c_lock
  change: 网线中缠着一个红绳串着、严重腐蚀的黄铜长命锁，刻着模糊的“长命富贵”，一侧有几道深深的抓痕
  'on': deck_old_net
- when: flag:sign_fs_zhang_far_gaze_1
  change: 船舷边多了一个背对甲板、朝远海站着的敦实人影。
views:
- key: deck_gunwale
  label: 船舷内侧
  features:
  - 船舷
  prompt: 渔船船舷边绑着一圈防撞的旧轮胎，内侧漆面有些凹痕和剐蹭，边角有修补痕迹
- key: deck_stove_pot
  label: 角落里的卡式炉和锅
  features:
  - 卡式炉和锅
  prompt: 甲板角落摞着一个小卡式炉和不锈钢锅，旁边是桶装矿泉水、一碟姜片和几瓶简单调味料
- key: deck_sunrise_sea
  label: 日出时的海面
  features:
  - 日出时的海面
  prompt: 海平面上金色的太阳刚刚跃出，海面由深红到亮橙再到流动的金色，一缕淡紫色的烟气在海面上弥漫开
- key: deck_old_net
  label: 缠成一团的旧渔网
  features:
  - 缠成一团的旧渔网
  prompt: 湿淋淋的甲板上摊着一大团缠在一起的旧渔网，网线纠结，挂着海水和泥污
```
