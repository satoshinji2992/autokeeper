# 渔具铺子

[返回《鲅鱼？鲅鱼！》](../../README.md) · 主持资料，含剧透

```yaml
id: tackle_stall
name: 渔具铺子
read_aloud: 渔具铺子简陋得多。一个抽旱烟的老头坐在小马扎上，面前铺着块油腻的蓝布，摊上挂着各种闪闪发亮的假饵、鱼线，还有一个用红布蒙着口的深色大陶罐。
keeper: '想看罐子里：老头懒洋洋掀开红布一角，腥气扑面，里面是一大团蠕动的肥硕沙蚕（可让胆小的调查员 0/1 理智检定，纯为吓唬，可选）。

  老头推销专业渔具，主打“来都来了，空军回去多可惜”。调查员买了东西，他从旧木盒里捻出几炷线香，用报纸包好作为赠品递过来，并交代老规矩（c_incense）。追问海和尚是什么，他简单解释（c_monk_lore）。

  '
features:
- 红布蒙口的陶罐
- 假饵和鱼线
- 旧木盒
art: tackle_stall
public: true
within: wharf
outdoors: true
ground: water
views:
- key: stall_clay_jar
  label: 红布蒙口的陶罐
  features:
  - 红布蒙口的陶罐
  prompt: 油腻的蓝布摊上放着一个深色大陶罐，罐口用红布蒙住，掀开一角能看到里面一大团蠕动的暗红色肥硕沙蚕，湿漉漉地纠缠在一起
- key: stall_lures_box
  label: 摊上的假饵与旧木盒
  features:
  - 假饵和鱼线
  - 旧木盒
  prompt: 小灯泡下，蓝布摊上挂着一排排闪闪发亮的假饵和成卷的鱼线，旁边放着一个磨旧了的小木盒，地上搁着旱烟杆
```
