# 海滨公园

[返回《失灵》](../../README.md) · 主持资料，含剧透

```yaml
id: seaside_park
name: 海滨公园
read_aloud: 奥克兰东部市区边缘的公园。水滑梯零件改成的长椅沿着步道排开，草坪上立着章鱼、鲸鱼之类的巨型橡胶玩偶。公园中心，一扇旧的公园大门作为纪念品立在那里，被一圈现代喷泉环绕。夜幕降临时，橡胶玩偶里的灯亮起柔光，家人们在草坪上散步运动，孩子们嬉笑着跑来跑去。
keeper: '前身是夜间水上公园（Nocte Waterpark），热门的家庭娱乐场所，1995 年关停，2003 年改建重开，受新一代家庭青睐，保留了大量旧设施作景观。腐化>30：光带在此形成折角。演出前这里未被腐蚀，悖论必须经现实世界前往，跟踪他们不容易。

  终演：如往日平静。悖论晚 8 点抵达，8 点 18 分开演，熟悉的节奏辐射影响心智。

  '
features:
- 公园大门
- 草坪上的橡胶玩偶
- 喷泉
- 步道长椅
art: seaside_park
scene_root: true
outdoors: true
ground: street
looks:
- when: at:jim:seaside_park & hour:20-24
  change: 公园中心的旧大门前，人群围成一个大圈，圈外架着一台亮着红灯的直播摄像机
views:
- key: seaside_old_gate
  label: 喷泉围着的旧大门
  features:
  - 公园大门
  - 喷泉
  prompt: 公园中心一扇旧的游乐园大门作为纪念品立着，被一圈现代喷泉环绕，水声哗哗。
- key: seaside_rubber_toys
  label: 草坪上的巨型玩偶
  features:
  - 草坪上的橡胶玩偶
  - 步道长椅
  prompt: 草坪上立着章鱼、鲸鱼形状的巨型橡胶玩偶，内部的灯透出柔光；步道边排着用水滑梯零件改成的长椅。
```
