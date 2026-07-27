# 6. 连续梁架生成

## 功能

保留 TwinGuide 当前导管 Q/P 和导板左右 A 点的选择结果，使用项目内部
连接算法生成经过导管 P 点的连续梁中心线，再扫掠为直径
4.60 mm 的圆截面梁。固定孔是否在最终融合后复切由导管实体模式决定。

## 输入

- `TemplateLinkPointPlan`：当前 TwinGuide 选择的导管上下 Q/P 与导板左右 A 点。
- `PointLinkingConfig`：梁半径、Hermite 张力、低梁下潜和扫掠分辨率。

- 可选 `PressBeamPointPlan`：内侧导管高端、两个牙位导板锚点及 Y 汇合点。
- 可选 `GuideTerminalUExtensionPlan`：导板末端双锚点、末端牙外包络和连续 U 型中心线。

## 输出

`PointLinkingPlan` 记录四根连续梁的左右 A 点、导管 P 点、离散中心线、
P 点索引和固定孔复切设置；启用按压梁时还记录三根 Y 臂、汇合点及独立半径。

## 依赖关系

本步只读取第 4 步锚点，不重新识别导管实体，也不改变锚点选择规则。
连续梁与切口导板、当前模式选择的导管实体一同进入 Blender 实体化和布尔融合。

## 处理逻辑

1. 保留两个牙位站位分配给同一导管的表面锚点 A，
   A 直接作为梁中心线端点，不再生成法向外移点 S。
2. 每个导管生成跨越两个站位的 `A− → P高 → A+` 和
   `A− → P低 → A+` 两根连续梁；两个导管共四根。
3. 高梁由两段五次 Hermite 曲线组成；两段在 P 点共享一阶导数，端点二阶导数为零。
4. 低梁先经过外层代理点形成平顺路线，再在 P 点附近 5.0 mm 弧长范围内局部下潜。左合并点沿原外层曲线左侧局部切向进入下潜段，右合并点沿右侧局部切向离开；只有深埋 P 点的切向使用左右合并点连线在导管横截面内的投影方向。
5. Blender 使用平行输运标架沿离散中心线扫掠圆截面，并封闭两端，不再拼接左右半梁。
6. 四根梁在左右导板端的最后 3.0 mm 均平滑渐粗至 1.08 倍半径；高、低梁
   共用的每个 A 接触位置只生成一组 1.08 倍半径根部球和曲面贴合脚，
   共 4 组。贴合脚按局部曲率在 100% 至 80% 间自适应缩放。
7. 主连接梁先以牙列外扩体做差集，裁掉进入牙体保护空间的部分。
   `generated` 模式与重建导管、导板正向融合后统一复切两个固定孔；
   `input` 模式把低位梁限制为仅嵌入真实外壁 1.45 mm，并在输入导管加入前
   复切导板和连接梁基础体的固定孔；随后融合受保护的输入导管，不再
   对最终整体复切固定孔。`generated` 模式在权威复切后保留最大连通体；
   `input` 模式不采用该规则，只删除体积小于 0.25 个融合体素的数值碎片。
8. 启用混合锚点按压梁时，从内侧导管高端 P 和两个牙位 S 分别连接到条件
   高度汇合点：导管 P 不低于三点中心时使用 P 等高面；P 较低时从三点中心
   沿导管正轴继续抬高。三臂与 1.12 倍半径汇合球一同参加最终融合。
9. 启用末端 U 型延伸梁时，先在已有导板末端取得 U/背 U 两个表面锚点；
   再用邻牙到末端牙的局部牙弓切向确定远中方向，从牙位识别的独立牙冠轮廓
   求远中和两侧外包络。中心线按梁半径、牙体净距和安全余量外扩，两条侧梁
   分别切向进入和离开远中回转段，最后执行牙列保护空间裁切与双根部强化。

## 配置参数

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `geometry.connector_diameter_mm` | 4.60 | 连接梁直径；配置可省略 |
| `geometry.connector_dental_clearance_mm` | 0.20 | 主连接梁相对牙列的保护净距 |
| `endpoint_tension` | 0.45 | Hermite 外端切向张力 |
| `contact_tension` | 0.90 | Hermite 导管 P 点切向张力 |
| `lower_approach_overlap_mm` | 1.45 | 低梁外层代理路线对导管壁的嵌入量 |
| `lower_dive_merge_arc_mm` | 5.0 | 低梁局部下潜与外层路线的单侧合并弧长 |
| `centerline_spacing_mm` | 0.30 | 中心线目标采样间距 |
| `curve_resolution` | 64 | 扫掠圆截面的分段数 |
| `recut_sleeve_bore` | `True` | `generated` 模式是否复切导管固定孔；`input` 模式不切输入导管 |
| `geometry.connector_guide_endpoint.root_radius_factor` | 1.08 | 连接梁导板端渐粗半径倍率 |
| `geometry.connector_guide_endpoint.transition_length_mm` | 3.00 | 双端渐粗过渡弧长 |
| `geometry.connector_guide_endpoint.foot_major_radius_mm` | 3.00 | 贴合脚配置主半径 |
| `geometry.connector_guide_endpoint.foot_minor_radius_mm` | 2.20 | 贴合脚配置次半径 |

## 结果示例

![连续梁架生成与固定孔复切](../images/point-linking.png)
