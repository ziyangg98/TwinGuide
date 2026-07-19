# 数据传递

## 配置与病例分析

`CaseConfig` 是文件路径和数值参数的唯一配置入口。
`analyze_case()` 读取网格，建立 `CaseAnalysis`，供各几何步骤共用。

## 步骤结果

| 上下文字段 | 结果类型 | 运行时行为 |
| --- | --- | --- |
| `sleeve_generation` | `SleeveGenerationResult` | 生成两个重建导套和导板局部标架 |
| `tooth_identification` | 尚未定义 | `skipped`；预留扩展 |
| `window_cutouts` | `WindowCutoutPlan` | 生成两个导孔、一个操作窗和可行的观察窗 |
| `template_link_points` | `TemplateLinkPointPlan` | 生成导套侧和导板侧锚点 |
| `press_beam_points` | 尚未定义 | `skipped`；预留扩展 |
| `point_linking` | `PointLinkingPlan` | 生成八条导套—导板曲线连接 |
| `clearance_adjustment` | 尚未定义 | `skipped`；预留扩展 |

预留扩展的结果类型将在对应几何模块实现时定义。在步骤未执行时，
`GenerationContext` 的对应字段为 `None`。当前 `window_cutouts` 只读取 `CaseAnalysis` 和
`SleeveGenerationResult`；它使用导板局部标架定位观察窗，尚未读取 `tooth_identification`。
目标数据流中，观察窗规划必须增加牙位和牙面区域输入。

## 建模与检查结果

`BuildArtifacts` 记录导出 STL 和渲染图路径。
`ValidationResult` 记录检查名称、通过状态和数值指标。

## 坐标与单位

- 网格点、轴线和几何选点使用世界坐标。
- 长度和距离的单位为毫米。
- `TemplateFrame` 提供牙科导板的横向、深度和法向坐标。
- 局部坐标只用于排序、方向判断和参数化，输出点仍使用世界坐标。
