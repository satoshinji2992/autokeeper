# 上锁的工坊

[返回《向火独行》](../../README.md) · 主持资料，含剧透

```yaml
id: locked_workshop
name: 上锁的工坊
read_aloud: 凉风拂面，屋檐漏进几道光。屋子正中有一块垫高的石板，略带坡度，石板下面的豁口里放着什么东西，也许是盆。对面墙边平放着一排盖着红布的东西，看上去……像是人。
keeper: '§250/§256：查看红布后，一个结实的工匠堵住门，“哟！我看你怎么偷偷摸摸的啊？喜不喜欢你的发现啊？”不战而降（§108，设 captured）或搏斗（§262，见工匠 NPC）。打倒他（§268）后可盘问尸体（§20）或离开。被他打倒（§2）就被拖走关押。三轮后可困难闪避绕过他逃跑，失败他再攻击一次；逃脱（§12）时把门板甩在他脸上，闪避打勾。

  '
features:
- 带坡度的石板
- 红布下的三具人体
art: locked_workshop
within: workshops
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_corpses
  change: 红布掀开,是三具枯瘦、缠着绷带的干尸,脚趾上挂着标签
  'on': workshop_red_cloth
- when: knows:players:clue:c_slab
  change: 石板下的豁口里能看清是个盆形容器
  'on': workshop_slab
views:
- key: workshop_red_cloth
  label: 红布下的人形
  features:
  - 红布下的三具人体
  prompt: 昏暗工坊对面墙边,一排平放着的东西盖着红布,轮廓分明像是人形,屋檐漏进几道光
- key: workshop_slab
  label: 带坡度的石板
  features:
  - 带坡度的石板
  prompt: 屋子正中垫高的一块略带坡度的石板,下方的豁口里放着一个盆样的东西
```
