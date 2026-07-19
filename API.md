# Twinguide 的 Python 接口

本文档记录生成程序公开的 Python 接口。几何长度使用毫米，
纯几何阶段不创建 Blender 对象。

## 1. 程序级入口

```python
from twin_guide import generate_guide, run_generation_process
```

### `run_generation_process(config) -> GenerationProcessResult`

- **输入**：已校验的 `CaseConfig`。
- **输出**：七个 `StageResult` 和一个 `GenerationContext`。
- **阶段状态**：`completed, skipped, completed, completed, skipped, completed, skipped`。
- **运行环境**：需要 Blender 提供的 Python 环境，因为第 1 步需读取 STL 网格。

### `generate_guide(config) -> BuildArtifacts`

根据第 4、6 步结果生成牙科导板。`BuildArtifacts.model_path` 是最终 STL，
`BuildArtifacts.image_paths` 是四视图和过程图。

## 2. 第 4 步：导套与牙科导板联建选点

```python
select_template_link_points(
    context: TemplateLinkPointContext,
    config: TemplatePointSelectionConfig,
) -> TemplateLinkPointPlan
```

### 输入

`TemplateLinkPointContext` 只允许包含：

- `case`：牙科导板表面采样和局部坐标系；
- `sleeves`：第 1 步导套结果；
- `cutouts`：第 3 步通道与窗口计划。

`TemplatePointSelectionConfig` 控制：

| 字段 | 含义 | 默认值 |
| --- | --- | ---: |
| `template_clearance_mm` | 候选点到窗口和通道的最小净距 | 1.2 |
| `connector_radius_mm` | 连接管半径，用于计算左右点最小跨度 | 1.2 |
| `surface_sample_limit` | 保留的近邻表面样本数 | 4096 |
| `candidate_limit` | 每侧参与成对评分的候选数 | 512 |

### 输出

`TemplateLinkPointPlan` 只包含：

- `sleeve_anchors`：每个导套的上下锚点；
- `template_points`：每个导套对应的牙科导板左右点；
- `diagnostics`：选点可行性及原因。

该输出不包含连接中心线、曲线管或 Blender 网格。

## 3. 第 6 步：选点连接

```python
link_selected_points(
    points: TemplateLinkPointPlan,
    config: PointLinkingConfig,
) -> PointLinkingPlan
```

`PointLinkingConfig` 字段：

| 字段 | 含义 | 默认值 |
| --- | --- | ---: |
| `radius_mm` | 连接管半径 | 必填 |
| `handle_factor` | 控制柄长度相对半径的上限倍数 | 3.0 |
| `curve_resolution` | Blender 曲线轴向细分 | 24 |
| `recut_sleeve_bore` | 连接管实体化后复切固定孔 | `True` |

`PointLinkingPlan` 输出两个导套的八条三次贝塞尔曲线。
`press_beam_links_included=False` 表示结果中不包含按压梁柱连接。

## 4. 调用示例

```python
from twin_guide import (
    PointLinkingConfig,
    TemplateLinkPointContext,
    TemplatePointSelectionConfig,
    link_selected_points,
    select_template_link_points,
)

point_plan = select_template_link_points(
    TemplateLinkPointContext(case, sleeve_result, cutout_plan),
    TemplatePointSelectionConfig(
        connector_radius_mm=config.geometry.connector_radius_mm
    ),
)
link_plan = link_selected_points(
    point_plan,
    PointLinkingConfig(radius_mm=config.geometry.connector_radius_mm),
)
```

## 5. 异常和缺失值约定

- 配置为负、零或搜索数量非正时抛出 `ValueError`。
- 病例分析与第 1 步导套结果不一致时抛出 `ValueError`。
- 选点失败时，第 4 步在结果中保留 `feasible=False` 和 `reason`。
- 第 6 步不接受不可行选点，会抛出 `ValueError`，不生成伪连接。
- 未实现阶段在 `GenerationProcessResult` 中记录为 `skipped`，其上下文字段保持 `None`。
