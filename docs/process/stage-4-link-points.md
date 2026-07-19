# 4. 导套与牙科导板联建锚点选择

## 功能

为每个导套选择两个导套侧锚点和两个牙科导板侧锚点，定义连接管的端点。

## 输入

`TemplateLinkPointContext` 包含 `CaseAnalysis`、`SleeveGenerationResult` 和 `WindowCutoutPlan`；
`TemplatePointSelectionConfig` 提供净距、间距和搜索数量。

## 输出

`TemplateLinkPointPlan` 包含导套侧上下锚点、牙科导板侧左右锚点和每个导套的可行性诊断。

## 依赖关系

该步骤读取第 1 步导套几何和第 3 步切口边界，为第 6 步提供连接端点。

## 处理逻辑

1. 将导套平台方向投影到轴线法平面，得到指向牙科导板的径向。
2. 在导套四分之一和四分之三高度选取主体外圆弧上的锚点。
3. 剔除与导孔和窗口净距不足的牙科导板表面样本。
4. 用导套轴向与径向的叉积定义左右，从两侧候选中选择最近且满足最小跨度的点对。
5. 按导套汇总两类锚点的可行性和失败原因。

## 配置参数

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `template_clearance_mm` | 1.2 | 候选点到导孔和窗口的最小净距 |
| `connector_radius_mm` | 1.2 | 连接管半径及左右点最小跨度的计算依据 |
| `surface_sample_limit` | 4096 | 按距离保留的表面样本上限 |
| `candidate_limit` | 512 | 每侧参与成对评分的候选上限 |

## 异常与诊断

病例分析与导套输出不一致时抛出 `ValueError`。外壁方向退化、锚点落入开口、
一侧无候选或无点对满足最小跨度时，在结果中记录 `feasible=False` 和 `reason`。

## 结果示例

![联建锚点选择](../images/link-point-selection.png)
