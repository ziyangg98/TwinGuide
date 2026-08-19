# 数据传递

## 配置与病例分析

`CaseConfig` 是文件路径和数值参数的唯一入口；`analyze_case()` 读取网格并建立
各步骤共用的 `CaseAnalysis`。每个种植位置的导柱参数由全局 `SleeveParameters`、
该位置的三项高度覆盖和编辑器三项高度调整合并得到；其余 9 项保持全局一致。
连接梁参数由 `GeometryParameters` 提供，其中 `anchor_selection` 控制第 4 阶段
导柱/导板锚点，`connector_path` 控制第 6 阶段中心线。第 3 阶段观察窗的布尔体和
QA 阈值由 `WindowParameters.observation_solver` 提供；这些参数都会进入生成缓存指纹。
`SleeveEstimate.c_opening_direction` 保存世界坐标单位向量。它是两导管中心连线
在各自轴线法平面上的投影，因此两个 C 口相对。

## 步骤结果

| 上下文字段 | 结果类型 | 运行时行为 |
| --- | --- | --- |
| `sleeve_generation` | `SleeveGenerationResult` | 按每个传统模板圆环的最终有效参数生成一对导柱，并建立导板局部标架 |
| `tooth_identification` | `ToothIdentificationResult` | 从 `case.yaml` 现场识别牙位并映射到导板 |
| `window_cutouts` | `CutoutPlan` | 生成导孔、操作窗以及 FDI 轴扫掠观察窗切除体 |
| `template_link_points` | `TemplateLinkPointPlan` | 生成单/多种植位连续路径锚点，末端病例可加入远中公共节点 |
| `press_beam_points` | `PressBeamPointPlan` | 可选生成混合导管锚点、三牙位锚点或末端 U 梁锚点的 Y 计划 |
| `point_linking` | `PointLinkingPlan` | 生成跨全部相邻导管的同侧连续梁，并可选加入三根 Y 型按压梁 |
| `clearance_adjustment` | `tuple[HandpieceAvoidancePlan, ...]` | 按配置顺序记录各手机的封闭运动包络 |

`window_cutouts` 读取 `CaseAnalysis` 和 `SleeveGenerationResult`。第 2 步调用牙位识别与
导板映射，要求结果状态完整、全部质量检查通过、上下颌一致，且结果对应本次导板和
牙列输入。输入与映射摘要记录在运行清单中。
第 3 步消费内存映射对象但不修改它；局部失败高度写入输出目录中的派生映射副本。

## 建模与检查结果

`BuildArtifacts` 记录导出 STL 和渲染图路径。
`ValidationResult` 记录检查名称、通过状态和数值指标。

## 坐标与单位

- 网格点、轴线和几何选点使用世界坐标。
- 长度和距离的单位为毫米。
- `TemplateFrame` 提供牙科导板的横向、深度和法向坐标。
- 局部坐标只用于排序、方向判断和参数化，输出点仍使用世界坐标。
