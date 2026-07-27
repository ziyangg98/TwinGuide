# 3. 导孔与窗口规划

## 功能

根据导套位置、操作结构和第 2 步 FDI 映射，规划导孔、操作窗和轴扫掠观察窗。

轴扫掠观察窗缓存指纹包含当前开口算法版本；仅修改实现而输入文件不变时也会
自动使旧缓存失效。牙位映射中的历史 `contour_following` 截面只保留为诊断，
不会作为轴扫掠 cutter 或牙位物理覆盖的约束。

## 输入

- `CaseAnalysis`：牙科导板表面、局部坐标系和操作结构尺寸。
- `SleeveGenerationResult`：导套轴线、固定孔直径和轴向范围。
- `ToothIdentificationResult`：只读牙位映射、观察窗 FDI 端点和语义轴。

## 输出

`WindowCutoutPlan` 包含两个导孔有限圆柱、操作窗，以及本阶段生成的轴扫掠组合 cutter 和报告。

## 依赖关系

导孔和操作窗依赖病例分析与导套结果。
本步的切口边界供后续联建锚点选择和功能区避让使用。

## 处理逻辑

1. 沿导套轴线生成带轴向余量的导孔切割体。
2. 对非导柱分量做局部主方向分解，选取靠近两导柱中点的近圆形结构，用其自身局部平面尺寸确定操作窗。
3. 直接使用映射报告给出的 FDI 窗口两端和语义轴；不重新识别或重排牙位。
4. 从轴向外部反向投射至导板最外边界，构造 90° 规则扇形 cutter，并做布尔、牙面可见性和连续走廊 QA。
5. 全局高度保持 0.2 mm。局部失败轴行按有效高度 0.5、1.0、2.0 mm 依次重试；通过即停止，否则保留最后一次结果。

## 配置参数

`sleeve.inner_diameter_mm` 和 `channel_axial_margin_mm` 分别控制导孔直径和轴向余量；
病例 YAML 的 `planning.operation_windows.tangent_margin_mm` 与
`bitangent_margin_mm` 分别控制操作窗沿切向和副切向的外扩余量，
`axial_margin_mm` 控制操作窗切除深度，`corner_radius_mm` 控制圆角。
副切向余量默认设为每侧 3.0 mm，因此操作窗短边默认为操作特征直径加 6.0 mm。
`observation_axis_drop_mm`、`observation_sweep_angle_degrees`、
`observation_local_failure_drop_targets_mm` 和
`observation_local_failure_transition_rows` 控制统一观察窗策略。
当前病例的导孔直径为 2.10 mm。
没有牙位映射时不生成观察窗，避免旧版几何缺口与 FDI 牙位脱节。

## 结果示例

![导孔与窗口规划](../images/cutout-planning.png)
