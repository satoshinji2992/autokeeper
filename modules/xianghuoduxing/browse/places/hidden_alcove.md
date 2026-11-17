# 书架后的壁龛

[返回《向火独行》](../../README.md) · 主持资料，含剧透

```yaml
id: hidden_alcove
name: 书架后的壁龛
read_aloud: 书架后面是一个仅容一人的壁龛，两边是隐蔽的书架，黑得几乎看不清。
keeper: '§153/§165：打开面向悬崖的窗才看得清，书都很旧，有手写本和奇特文字，浏览一遍要一星期。图书馆使用检定，可孤注一掷；孤注一掷失败即§190 被村民发现；大失败不能孤注，只得到德比诗集（§184）。

  §159：不开窗直接抓几本出去，到工坊后面阴影里看，只能带走德比诗集。

  §207：火之卷太大，无法藏在身上带走。

  '
features:
- 暗红三角书套的典籍
- 黑封面诗集
- 手写抄本
- 面向悬崖的窗
art: hidden_alcove
within: village_hall
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_fire_tome
  change: 书页翻开,满是星图、炼金符号和排列整齐的诗句
  'on': alcove_fire_tome
views:
- key: alcove_fire_tome
  label: 红三角书套的典籍
  features:
  - 暗红三角书套的典籍
  prompt: 狭窄壁龛里的旧书架上,一部厚重巨大的旧书,书套上有暗红色的三角形,周围是些手写本
```
