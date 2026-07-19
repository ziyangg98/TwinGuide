# Twinguide 程序架构

## 1. 总体目标

程序以强类型数据对象传递各步结果。每步只读取已声明的输入，
几何计算与 Blender 实体化分层实现。

```text
CaseConfig + 原始 STL
          |
          +--> [1 导套识别/生成] --------------------+
          |                                             |
          +--> [2 牙位识别：待实现]                     |
          |                                             v
          +--> [牙科导板只读分析] --> [3 窗口切口] --> [4 联建选点]
                                                        |
                    [5 按压梁柱选点：待实现] -----+
                                                        v
                                              [6 选点连接：实验]
                                                        v
                                              [7 避让调整：待实现]
                                                        v
                                                最终几何计划
```

牙科导板只读分析是公共前置服务，不是业务阶段。它提供牙科导板采样点、局部坐标系和空间查询，
不决定牙位、窗口或联建点。

## 2. 程序级输入与输出

### 输入

- `CaseConfig`：病例编号、STL 路径、几何参数、窗口参数、渲染参数和输出目录。
- `template`：牙科导板 STL。
- `guide_sleeve_assembly`：包含导套候选件的装配体 STL。
- 验证模式可额外提供牙科手机 STL，生成模型时不读取该文件。

### 生成流程输出

`GenerationProcessResult` 是结构化运行记录，包含：

- 固定长度的七个 `StageResult`；
- 每步的编号、键名、中文功能、成熟度、实现版本、依赖和输出键；
- `completed` 阶段的强类型输出；
- `skipped` 阶段的跳过原因；
- `completed_outputs`：已完成阶段的键值表。

`generate_guide()` 根据第 4、6 步结果导出导套—牙科导板结构。

## 3. 阶段输入输出契约

### 阶段 1：导套的识别与生成

**公开接口**

```python
recognize_and_build_sleeves(inputs: SleeveGenerationInputs) -> SleeveGenerationResult
```

**输入**

- 装配体分离后的连通分量；
- 只用于确定导套朝向和左右顺序的牙科导板 BVH、采样点和中心。

**输出**

- 两个 `GuideSleeve`；
- 用于稳定左右顺序的牙科导板局部坐标系；
- 每个导套的轴线、轴原点、高度、内外半径、平台几何和开口角；
- 闭合重建实体所需的完整参数；
- 参数诊断与输入—重建双向误差。

该结果不包含牙位编号、窗口、联建点、按压梁柱点或避让调整。

### 阶段 2：牙位识别

**预留接口**

```python
identify_tooth_positions(context: GenerationContext) -> ToothIdentificationResult
```

该步尚未实现，直接调用时抛出 `NotImplementedError`。其输出将使用牙位标签与几何对象的映射。

### 阶段 3：操作窗和观察窗

```python
plan_window_cutouts(
    case: CaseAnalysis,
    sleeves: SleeveGenerationResult,
) -> WindowCutoutPlan
```

- 输入：牙科导板分析、`SleeveGenerationResult`和窗口配置。
- 输出：两个导套通道、一个操作窗和可用观察窗的纯几何计划。
- 输入不包含牙位识别结果。

### 阶段 4：导套与牙科导板联建选点

```python
select_template_link_points(
    context: TemplateLinkPointContext,
    config: TemplatePointSelectionConfig,
) -> TemplateLinkPointPlan
```

- 输入：牙科导板分析、阶段 1 导套、阶段 3 窗口计划。
- 输出：每个导套的两个导套侧锚点、两个牙科导板侧锚点和选择诊断。
- 不包含中心线或连接网格；这些只属于第 6 步。

### 阶段 5–7

第 5、7 步分别保留 `select_press_beam_points()` 和 `adjust_clearance()` 接口，
运行时跳过。第 6 步的 `link_selected_points()` 输入第 4 步计划和
`PointLinkingConfig`，输出贝塞尔曲线、半径和重新切孔标志；
`press_beam_links_included=False` 表示结果中不包含第 5 步连接。
曲线管实体化后重新切除导套固定孔，防止新增连接侵占导套中间空缺。

## 4. 依赖和状态规则

- 稳定：默认执行，需有单元、Blender 集成和当前病例回归测试。
- 实验：默认执行，输出可供下游实验，但不声明已验收。
- 待实现：编排器跳过，必须给出原因，不生成输出。
- 一个阶段只能依赖 `StageDefinition.requires` 中声明的数据。
- 阶段几何输出使用不可变数据类；编排上下文只负责组合，不改写已完成结果。
- 最终建模和独立验证必须使用阶段 1 的 `GuideSleeve.parameters` 重建同一个完整导套实体，不得另行生成简化圆柱代替。
- 任何成熟度变更都要同步实现版本、测试和本文档。

## 5. 协作开发流程

1. 只在自己负责的阶段模块内实现算法。
2. 需要新的上游数据时，先更新数据契约和 `requires`，不直接导入上游私有函数。
3. 为新输出建立不可变结果类型，并在本文档记录单位、坐标系和缺失值语义。
4. 添加阶段内单元测试、与直接上下游的契约测试和当前病例回归测试。
5. 实现可运行但未完成验收时标为实验状态；验收后改为稳定状态。
