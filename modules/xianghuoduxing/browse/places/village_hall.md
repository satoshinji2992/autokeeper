# 村会堂

[返回《向火独行》](../../README.md) · 主持资料，含剧透

```yaml
id: village_hall
name: 村会堂
read_aloud: 村会堂在北面较低的山脊上，锡尔伯里街东头，背靠悬崖，是村里最大的建筑。大厅连着配楼，东边面朝灯塔的一扇窗被砖砌死了。右边一扇门上写着“私用”，左边房间里摆着长凳和一块布告板。
keeper: '§84：上午锁门，灰衣女人说文特斯上午不开门，下午再来；问电报，她说“不知道，我们要拍给谁？”。§11/§24/§37：下午敲“私用”门，或看布告板时，克莱德·文特斯出来请调查员喝咖啡。

  §126（D3 白天潜入）：绕北边崖壁从后面接近，最西边一扇窗的百叶松弛可以钻进去，进的是集会室，外面村民正兴奋地议论；推开“私用”门进 private_room。§207/§213/§190：在暗室逗留后听到大门钥匙声，冲向窗户做敏捷检定，成功跳窗未被发现，失败村民涌入，文特斯说“原来您在这里”（设
  captured）。

  '
features:
- 布告板
- 被砖砌死的窗下地板
- “私用”门
art: village_hall
public: true
aliases:
- 会堂
scene_root: true
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_board
  change: 孩子那块板上有一张少了图钉、倒吊着的画,画着一只巨鸟扑向一个小村
  'on': hall_board
views:
- key: hall_board
  label: 布告板
  features:
  - 布告板
  prompt: 明亮的集会室墙上并排挂着两块布告板,一块贴着旧广告和泛黄的价目表,另一块钉满孩子们五颜六色的涂鸦
```
