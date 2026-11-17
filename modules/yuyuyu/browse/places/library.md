# 少儿图书馆

[返回《鱼！鱼！鱼！》](../../README.md) · 主持资料，含剧透

```yaml
id: library
name: 少儿图书馆
read_aloud: 从热闹的街角折入侧巷，灰白色的外墙，方方正正的建筑，一座刚投入使用没几年的现代馆舍出现在眼前。借阅台后的管理员正低头为几册图书加盖日期戳。《冒险小虎队》《鸡皮疙瘩》和《皮皮鲁历险记》的封面已经有些破破烂烂，《丁丁历险记》的书脊裂开了好几道口子，你们好不容易才把注意力从这些书身上移开。《中外神话传说》和《伊索寓言精选》紧紧地挤在一处，再过去是《纳尼亚传奇》和国际大奖小说书系，最边上斜靠着《黄冈小状元达标卷》和《小学生必背古诗
  70 篇》。
keeper: 调查时长 60 分钟。阮施宁常来这里。管理员昨天见过她。阅览处有当天的《石头屿晚报》。图书馆使用成功可找到几本崭新的深海绘本。
features:
- 借阅台
- 书架
- 新到的绘本
- 报刊架
art: library
public: true
aliases:
- 图书馆
- 囡仔图书馆
scene_root: true
outdoors: false
ground: building
looks:
- when: knows:players:clue:c_picture_book
  change: 书架间多出几本崭新的深海绘本，封面是深蓝靛紫的海和一群圆滚滚、大眼睛的鱼形小生物
  'on': library_bookshelf
- when: flag:sign_fs_city_cough_1
  change: 阅览处的桌边多出两三个弯着腰、手按在胸口上的大人身影。
views:
- key: library_bookshelf
  label: 少儿书架
  features:
  - 书架
  - 新到的绘本
  prompt: 一排少儿书架，《冒险小虎队》《鸡皮疙瘩》《皮皮鲁历险记》封面破破烂烂，《丁丁历险记》书脊裂了口子，最边上斜靠着练习卷和古诗本
- key: library_newspaper
  label: 报刊架上的晚报
  features:
  - 报刊架
  prompt: 阅览处的报刊架上夹着当天的《石头屿晚报》，头版标题写着石坡尾清淤发现老旧闸门渗漏，版面角落有天气栏和潮汐表
- key: library_desk
  label: 借阅台
  features:
  - 借阅台
  prompt: 借阅台上摞着几册待还的书，一枚日期戳和印泥盒放在一旁，台后的管理员正低头盖戳
```
