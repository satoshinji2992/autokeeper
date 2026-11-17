# 结实的工匠

[返回《向火独行》](../../README.md) · 主持资料，含剧透

```yaml
id: craftsman
name: 结实的工匠
role: 村里的工匠，守着上锁的工坊
look: 结实，系深色围裙，留大胡子
personality: 粗野，护着工坊里的东西
voice: 嗓门粗，爱冷笑着反问
goal: 不让外人看到工坊里的尸体
knows:
- 红布下的三具尸体是村民，自然死亡，节日要用（§20）
- 工匠们让西拉斯偶尔捎包裹（§96）
clues:
- c_craftsmen_nervous
gates:
  c_craftsmen_nervous:
    skill: 心理学
location: workshops
portrait: craftsman
stats:
  敏捷: 41
  格斗(斗殴): 35
  生命: 12
called: 系深色围裙的大胡子
attacks:
- 格斗(斗殴) 35%，伤害 1D3+1D4
```
