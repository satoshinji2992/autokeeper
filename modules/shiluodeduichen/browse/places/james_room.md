# 詹姆斯的宿舍

[返回《失落的对称》](../../README.md) · 主持资料，含剧透

```yaml
id: james_room
name: 詹姆斯的宿舍
read_aloud: 整个房间一片狼藉，乱得离奇。你们知道詹姆斯有点不爱收拾，却从没想到会乱成这样。混乱似乎都是从书桌开始的：书本、纸页堆成小山，桌子背后的墙上贴满了图形和草图。
keeper: 门边挂着第二把钥匙，可从里面开门。书桌上的逾期通知来自霍尔科姆夫人。草图辨认、侦查线索见 clues。搜房几分钟即可免检找到一封旧信和科拉阿姨的地址。
features:
- 书桌
- 墙上的图形和草图
- 书桌边的乱堆
- 一封旧信
- 门边的钥匙钩
art: james_room
within: dorm
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_postcard
  change: 图形纸页底下露出半张风景明信片的一角
  'on': james_desk
views:
- key: james_desk
  label: 堆满纸页的书桌
  features:
  - 书桌
  prompt: 凌乱不堪的学生书桌，书本和写满算式的纸页堆成小山，几张印着图书馆抬头的逾期通知压在上面，纸堆正中一张纸上用钢笔画着一个圆形里的奇怪几何图形
- key: james_wall_sketches
  label: 墙上的几何草图
  features:
  - 墙上的图形和草图
  prompt: 书桌背后的墙上密密麻麻贴满纸张，画着古典风格的几何作图：圆、三角形、直线构造，许多图形一层套一层地缩小重复，边上写满细小的注释
```
