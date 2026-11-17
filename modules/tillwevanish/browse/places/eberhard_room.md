# 艾伯哈德的卧室

[返回《直到我们灰飞烟灭》](../../README.md) · 主持资料，含剧透

```yaml
id: eberhard_room
name: 艾伯哈德的卧室
read_aloud: 卧室里香水味浓得呛人，梳妆台上摆满瓶瓶罐罐，衣柜门半掩着，床上扔着几件丝绸衣物。
keeper: '衣柜里是黑丝、紧身胸衣、高跟皮靴、各种假发和化妆品、假胸，其中有符合相关案件目击证人描述的衣服。

  银行保险柜的钥匙在这个房间里。打字转录本与图册也可能放在这里（AutoKeeper 默认放在塞格利办公室，守秘人可改放此处）。

  '
features:
- 衣柜
- 梳妆台
art: eberhard_room
within: manor
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_wardrobe
  change: 衣柜敞开：成排的黑丝袜、紧身胸衣、高跟皮靴、几顶假发和一副假胸，挂着几件艳俗的女式外衣
  'on': eberhard_wardrobe
- when: knows:players:clue:c_key_found
  change: 抽屉里躺着一把带编号牌的银行保险柜钥匙
  'on': eberhard_vanity
views:
- key: eberhard_wardrobe
  label: 半掩的衣柜
  features:
  - 衣柜
  prompt: 半掩的雕花衣柜门缝里露出丝绸裙摆和蕾丝花边，柜门上挂着一件深色外套，地上散着香水瓶
- key: eberhard_vanity
  label: 摆满瓶罐的梳妆台
  features:
  - 梳妆台
  prompt: 梳妆台镜子前挤满香水瓶、粉盒、口红和眼线笔，粉扑上沾着脂粉，几个小抽屉半开着
```
