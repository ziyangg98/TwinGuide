# 4. 导套与牙科导板联建锚点选择

## 功能

为每个导套选择两个导套侧锚点和两个牙科导板侧锚点，定义连接管的端点。

## 输入

`TemplateLinkPointContext` 包含 `CaseAnalysis`、`SleeveGenerationResult` 和 `WindowCutoutPlan`；
`TemplatePointSelectionConfig` 提供净距、间距和搜索数量。

## 输出

`TemplateLinkPointPlan` 包含导套侧上下锚点和牙科导板侧左右锚点。

## 依赖关系

该步骤读取第 1 步导套几何和第 3 步切口边界，为第 6 步提供连接端点。

## 处理逻辑

1. 将导套 C 口方向投影到轴线法平面并取反，得到指向主体圆弧侧的径向。
2. 在导套四分之一和四分之三高度选取主体外圆弧上的锚点。
3. 剔除与导孔和窗口净距不足的牙科导板表面样本。
4. 用导套轴向与径向的叉积定义左右，从两侧候选中选择最近且满足最小跨度的点对。
5. 按导套汇总两类锚点。

## 配置参数

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `template_clearance_mm` | 1.2 | 候选点到导孔和窗口的最小净距 |
| `connector_diameter_mm` | 2.30 | 导柱与牙科导板之间的连接柱直径 |
| `surface_sample_limit` | 4096 | 按距离保留的表面样本上限 |
| `candidate_limit` | 512 | 每侧参与成对评分的候选上限 |

窗口和导孔避让后必须仍有分居两侧且满足最小跨度的表面点对。

## 结果示例

![联建锚点选择](../images/link-point-selection.png)
