# TwinGuide 建模架构与实现边界

本页说明七阶段的协作方式和模块边界。各阶段的符号、公式、
分支条件、输出和质量检查见[生成过程](../process/index.md)中的对应页面。

## 系统边界

TwinGuide 把病例输入转换为可制造的双导管牙科导板。
`case.yaml` 是唯一配置入口，同时定义：

- 病例输入路径与上下颌、FDI 牙位和已确认坐标轴；
- 标准导管尺寸、窗口、锚点和连接梁参数；
- 按压梁、跨组件桥接、末端 U 型延伸、远中公共节点和手机避让；
- 病例的审核状态。

生成结果是按种植位参数重建的导柱、原导板、主连接梁、按压梁与显式特殊结构
的统一封闭网格，以及经过复切的导孔、操作窗、观察窗和手机避让空间。

## 七阶段合同

| 阶段 | 算法说明 | 输入 | 类型化输出 | 模块边界 |
| --- | --- | --- | --- | --- |
| 1 | [传统模板圆环识别与导柱重建](../process/stage-1-sleeves.md) | 传统模板圆环、规划参数、全局标准值与种植位三项高度覆盖 | `SleeveGenerationResult` | 不读取导管装配体 |
| 2 | [牙位识别与导板映射](../process/stage-2-teeth.md) | 牙列、导板、病例牙位语义 | `ToothIdentificationResult` | 不生成梁或切除体 |
| 3 | [导孔与窗口规划](../process/stage-3-cutouts.md) | 导管位姿、FDI 轴与导板表面 | `CutoutPlan` | 不选择连接锚点 |
| 4 | [主连接梁锚点](../process/stage-4-link-points.md) | 标准导管、牙位局部标架、导板 | `TemplateLinkPointPlan` | 不生成曲线网格 |
| 5 | [按压梁锚点与汇合点](../process/stage-5-press-beam.md) | 主锚点、牙位、可选末端结构 | `PressBeamPointPlan` | 不改写第 4 阶段锚点 |
| 6 | [连续梁架生成](../process/stage-6-linking.md) | 所有有序锚点与特殊拓扑 | `PointLinkingPlan` | 不重新识别病例语义 |
| 7 | [手机摆动避让](../process/stage-7-clearance.md) | 手机 STL、止挡报告、摆角 | `tuple[HandpieceAvoidancePlan, ...]` | 不保护进入包络的结构 |

七阶段控制器先调用 `analyze_case()` 导入传统模板和患者牙列，从模板定位圆环生成
各个种植位的双导导柱并完成第 1 阶段病例分析，再按顺序调用阶段函数；特殊桥接和 U 型延伸计划在第 4 阶段锚点
计算前插入，最终全部结果写入 `GenerationContext`。一个阶段不能从下游最终 STL
反推或修改上游语义。

## 规划层、Blender 层与验证层

### 阶段编排与几何分析层

`generation_process.py` 调度七个类型化阶段。这一层负责所有业务决策：
导管候选、牙位语义、窗口范围、锚点、路径拓扑、手机运动轴与枢轴。
其输出是点、轴、曲线、封闭切除体和质量指标。它不是脱离 Blender 的纯计算层：
`analyze_case()` 通过 `bpy` 导入 STL、清空场景、拆分连通分量并采样网格，部分窗口
和锚点计算也消费这些 Blender 网格对象。因此 `process`、`generate` 和 `validate`
都必须由 Blender Python 启动。

### Blender 实体化层

`guide_generation.py` 和 `blender/` 只消费几何计划，完成标准导管、
圆梁、强化脚、差集切除体、体素融合与 STL 序列化。

### 验证层

验证器使用同一配置和公开计划重建期望位置，但独立读取最终 STL
计算闭合性、导管保留、导孔贯通、梁中心线覆盖、窗口净距和特殊结构检查。
验证器不修改待检查模型，也不把生成阶段的“计划通过”当作最终几何通过；但它会
重新执行 `run_generation_process()`，因此可能在病例默认输出目录中复用或刷新
第 2 阶段缓存，并重写七个阶段 JSON。

## 实体布尔顺序

整体实体化顺序固定为：

```text
原导板 - 初始导孔/操作窗/观察窗
→ 融合标准导管、主梁、按压梁和特殊结构
→ 复切导孔和观察窗
→ 手机运动包络差集
→ 只保留有效主连通体
→ STL 导出与独立验证
```

复切位于整体融合之后，是因为预埋梁和体素融合可能再次填入导孔或窗口。
手机避让位于复切之后，使最终空间约束对导板、导管和梁一律生效。

## 特殊拓扑的接入原则

特殊病例不建立第二套生成管线，而是产生第 4–6 阶段可消费的
显式锚点或路径：

- 两块导板：两条跨组件路径；
- 末端有牙但导板过短：一条经过审核的 U 型延伸路径；
- 末端缺牙且无导板覆盖：四条主梁共用的远中自由节点。

U 型延伸和远中公共节点表达两种不同的末端语义，并且互斥。
三类特殊病例的适用条件、配置、几何公式、净距保护和失败条件见
{doc}`../process/special-topologies`。

## 阶段产物与诊断

`generate` 将阶段产物写入 `output/<case_id>/`：

- `stage-NN-<name>.json`：结构化输入、参数、结果、质量检查与产物索引；
- `stage-NN-<name>.png`：完成阶段的几何结果；
- `.cache/stage-NN-<name>/`：可重算网格、报告与指纹。

JSON 记录数值结果与质量检查，PNG 展示对应的几何结果。

## 成熟度与完成条件

第 1 阶段是当前稳定的定位与按种植位参数重建入口；第 2–7 阶段仍标记为实验。
完整回归包括：

- Ruff 和全部单元测试通过；
- Sphinx 使用 `-E -W --keep-going` 严格构建通过；
- Blender 后端、端到端生成与最终 STL 验证通过；
- 12 个正式病例完整回归，每个定位圆环稳定生成一对导柱；
- `git diff --check` 无空白错误。

## 相关文档

各类配置、算法和验证细节由下列页面维护：

| 内容 | 页面 |
| --- | --- |
| 单一 YAML、输入、审核状态和参数表 | {doc}`configuration` |
| 导柱定位、方向、C 口与按种植位参数重建 | {doc}`../process/stage-1-sleeves` |
| 牙位投影、分割、FDI 映射与坐标一致性 | {doc}`../process/stage-2-teeth` |
| 导孔、操作窗、轴扫观察窗与局部修正 | {doc}`../process/stage-3-cutouts` |
| Q/P、牙位 T 点、旋转射线和导板锚点分配 | {doc}`../process/stage-4-link-points` |
| 三种 Y 型按压梁锚点与汇合约束 | {doc}`../process/stage-5-press-beam` |
| 单/多种植位主梁、五次曲线、单种植位低端下潜、扫掠和强化 | {doc}`../process/stage-6-linking` |
| 两分量桥接、末端 U 延伸和远中公共节点 | {doc}`../process/special-topologies` |
| 手机旋转、姿态采样、包络与差集 | {doc}`../process/stage-7-clearance` |
| 实体布尔顺序 | 本页“实体布尔顺序” |
| 输出文件、阶段 JSON/PNG 和缓存 | {doc}`outputs` |
| 拓扑、导管、梁、导孔、观察窗和特殊结构验证 | {doc}`validation` |
| 耗时来源和常见失败定位 | {doc}`troubleshooting` |
| 新病例从输入到生产回归 | {doc}`running` 的“新病例落地顺序” |

## 代码入口索引

| 职责 | 代码入口 |
| --- | --- |
| YAML 数据类型、加载、解析和业务校验 | `src/twin_guide/config/` |
| 七阶段控制器和上下文 | `src/twin_guide/generation_process.py` |
| 病例分析和导柱定位 | `src/twin_guide/case_analysis.py`、`src/twin_guide/template_ring_estimation.py`、`src/twin_guide/guide_post_positioning.py` |
| 牙位识别和映射 | `src/twin_guide/tooth_identification.py`、`src/twin_guide/tooth_mapping/` |
| 导孔和窗规划 | `src/twin_guide/window_cutouts.py`、`src/twin_guide/observation_window_engine/` |
| 导管 Q/P 和导板锚点 | `src/twin_guide/sleeve_anchors.py`、`src/twin_guide/template_link_points.py` |
| 特殊锚点与路径 | `src/twin_guide/guide_component_bridge.py`、`src/twin_guide/guide_terminal_u_extension.py`、`src/twin_guide/terminal_distal_common_node.py` |
| 按压梁锚点 | `src/twin_guide/press_beam_points.py` |
| 连续曲线与结构路径 | `src/twin_guide/point_linking.py` |
| 手机摆动计划 | `src/twin_guide/clearance_adjustment.py` |
| Blender 实体化和导出 | `src/twin_guide/blender/guide_modeling.py` |
| 最终 STL 独立验证 | `src/twin_guide/guide_validation.py` |

当前 12 个规范病例的实际阶段状态、验证指标、特殊结构覆盖和最终图见
{doc}`case-results`。
