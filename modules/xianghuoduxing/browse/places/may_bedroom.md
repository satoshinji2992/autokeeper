# 梅的卧室

[返回《向火独行》](../../README.md) · 主持资料，含剧透

```yaml
id: may_bedroom
name: 梅的卧室
read_aloud: 卧室里堆着脏衣服、露丝的课本和廉价小说，床下露出一个旧洋娃娃。
keeper: '§83：侦查。成功（§95）见活板门；失败（§89）只在抽屉里找到结婚照，可孤注一掷重掷侦查，成功§95，失败§101：梅回来了，“那么，你明白的。”一大群村民从她身后涌出制服调查员（设
  captured）。大失败不能孤注一掷。

  '
features:
- 抽屉
- 床下地毯
- 脏衣和课本
art: may_bedroom
within: ledbetter_house
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_trapdoor
  change: 床被拉开,地毯掀起,露出一扇通向黑暗的木活板门
  'on': bedroom_rug
views:
- key: bedroom_rug
  label: 床下的地毯
  features:
  - 床下地毯
  prompt: 凌乱的卧室,脏衣服堆在地上,课本和廉价小说散落在被褥上,床下露出一个旧洋娃娃和一角地毯,地板上有一道浅浅的拖痕
```
