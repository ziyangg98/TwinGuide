# 数据传递

## 配置与病例分析

`CaseConfig` 是文件路径和数值参数的唯一配置入口。
`analyze_case()` 读取网格，建立 `CaseAnalysis`，供各几何步骤共用。
导管的八个几何参数由 `SleeveParameters` 提供，连接柱直径由
`GeometryParameters` 提供。
`SleeveEstimate.c_opening_direction` 保存世界坐标单位向量。它是两导管中心连线
在各自轴线法平面上的投影，因此两个 C 口相对。

## 步骤结果

| 上下文字段 | 结果类型 | 运行时行为 |
| --- | --- | --- |
| `sleeve_generation` | `SleeveGenerationResult` | 按每个种植位识别一对具有轴孔的导管，并按模式重建或保存输入实体及导板局部标架 |
| `tooth_identification` | `ToothIdentificationResult` | 从 `case.yaml` 现场识别牙位并映射到导板 |
| `window_cutouts` | `WindowCutoutPlan` | 生成导孔、操作窗以及 FDI 轴扫掠观察窗 cutter |
| `template_link_points` | `TemplateLinkPointPlan` | 生成单/多种植位连续路径锚点，末端病例可加入远中公共节点 |
| `press_beam_points` | `PressBeamPointPlan` | 可选生成混合导管锚点、三牙位锚点或末端 U 梁锚点的 Y 计划 |
| `point_linking` | `PointLinkingPlan` | 生成跨全部相邻导管的同侧连续梁，并可选加入三根 Y 型按压梁 |
| `clearance_adjustment` | 手机 STL、止挡报告、左右摆动参数 | 封闭运动包络；未配置时 `skipped` |

可选步骤未执行时，`GenerationContext` 的对应字段为 `None`。`window_cutouts` 读取 `CaseAnalysis` 和
`SleeveGenerationResult`。配置 `tooth_identification` 时，第 2 步调用统一识别与
导板映射库，要求结果状态完整、全部 QA 为真、上下颌一致，且结果使用的导板和
口扫与当前输入相同。输入与映射报告摘要记录在本次运行清单中，陈旧结果不能复用。
第 3 步消费内存映射对象但不修改它；局部失败高度写入输出目录中的派生映射副本。

## 建模与检查结果

`BuildArtifacts` 记录导出 STL 和渲染图路径。
`ValidationResult` 记录检查名称、通过状态和数值指标。

## 坐标与单位

- 网格点、轴线和几何选点使用世界坐标。
- 长度和距离的单位为毫米。
- `TemplateFrame` 提供牙科导板的横向、深度和法向坐标。
- 局部坐标只用于排序、方向判断和参数化，输出点仍使用世界坐标。
