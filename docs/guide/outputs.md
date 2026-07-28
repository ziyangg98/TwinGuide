# 输出结果

生成结果默认写入 `output/<case_id>/`。`generate --output DIRECTORY` 可以为
本次运行指定其他目录。全部阶段完成时，生成目录为：

```text
output/<case_id>/
  twin_guide.stl
  guide_iso.png
  guide_top.png
  guide_bottom.png
  guide_side.png
  stage-01-sleeve-reconstruction.json
  stage-01-sleeve-reconstruction.png
  ...
  stage-07-clearance-adjustment.json
  stage-07-clearance-adjustment.png
  .cache/
    stage-01-sleeve-reconstruction/
    ...
    stage-07-clearance-adjustment/
```

`generate` 为七个阶段记录同名 JSON，并为完成阶段生成对应 PNG。

## 最终模型和标准视图

| 文件 | 含义 | 使用方式 |
| --- | --- | --- |
| `twin_guide.stl` | 融合导板、标准重建导管和梁架，复切导孔/观察窗并执行手机避让后的最终封闭网格 | 用于制造和 `validate` |
| `guide_iso.png` | 最终 STL 等轴视图 | 快速查看整体拓扑 |
| `guide_top.png` | 牙合侧视图 | 检查导孔、导管和两侧结构 |
| `guide_bottom.png` | 组织侧视图 | 检查导板内面和梁的底部关系 |
| `guide_side.png` | 侧视图 | 检查轴向高度、按压梁和避让范围 |

几何验收结果由 `validate` 输出。

## 阶段 JSON 公共契约

七个阶段 JSON 共用 `twin-guide.stage-result/1.0` 顶层结构：

| 顶层字段 | 内容 |
| --- | --- |
| `schema_version` | 阶段文档契约版本 |
| `stage` | `number`、`key`、中文 `title`、`status`、`maturity` 和 `implementation_version` |
| `case` | 病例 `id` 和运行时上下颌 `jaw`；第 2 阶段可附加显示信息 |
| `inputs` | 该阶段需要的上游结果以及本次使用的牙列、导板和导管装配体路径 |
| `parameters` | 该阶段真正消费的主要配置，而不是整份 YAML 复制 |
| `result` | 类型化阶段结果的 JSON 序列化；Blender 运行时网格不进入契约 |
| `quality` | `passed`、布尔 `checks`、少量可审核 `metrics` 和跳过/失败 `reason` |
| `artifacts` | 本 JSON 的绝对路径以及实际存在的阶段 PNG 路径 |

`quality.passed` 表示阶段文档中的类型结果和业务指标是否齐备；它不等于
最终 STL 已通过 `validate`。

## 各阶段结果

| 阶段文件 | `result` 主要内容 | `quality.metrics` 主要指标 | PNG 表达内容 |
| --- | --- | --- | --- |
| `stage-01-sleeve-reconstruction` | 逐装配体识别的导管位姿、局部标架和标准重建导管 | 导管数、导管索引、长度和导孔直径 | 蓝色输入装配体与灰色标准重建导管 |
| `stage-02-tooth-mapping` | 牙弓坐标框、FDI 顺序、现存/缺失牙、导板映射和观察窗端点 | 现存牙数、FDI 列表、缺失牙、观察窗数和映射检查 | 实测牙冠投影、FDI 中心、牙弓距离和观察窗范围 |
| `stage-03-cutout-planning` | 导孔、操作窗和 FDI 轴扫观察窗切除体 | 三类切除体数量、观察窗 ID、切除体积、最小轴线净距和局部修正状态 | 已完成切口的蓝色导板与灰色导管 |
| `stage-04-anchor-selection` | 导管 Q/P、导板射线锚点、轨迹和特殊端点 | 导管锚点数、导板锚点数和轨迹数 | 红色导管锚点、黄色导板锚点及射线/轨迹 |
| `stage-05-press-beam` | 按压梁三个锚点、汇合点、半径和选择模式 | 连接类型、导板锚点数、最小汇合角、导管距离和梁径 | 金色按压梁、三锚点和 Y 型汇合关系 |
| `stage-06-structure-linking` | 四根主梁、按压梁与特殊连接路径 | 主连接数、按压梁连接数、梁径、导孔复切和牙列修剪标记 | 金色连续梁架、灰色导管和蓝色导板 |
| `stage-07-clearance-adjustment` | 每个手机的旋转轴、枢轴、采样角和摆动包络路径 | 手机数、姿态数、角度范围和额外净距 | 半透明棕色真实包络、黑色旋转轴和红色枢轴 |

第 2 阶段的 JSON 同时包含牙位识别与映射质量检查。

## `.cache` 内部产物

`.cache/stage-NN-<name>/` 只存放可重算的中间网格、数组、原始图和详细报告，
不是下游公开接口。常见内容包括：

- 第 2 阶段的牙冠投影 `.npz`、接触弦报告、牙位识别和导板映射 JSON；
- 第 3 阶段的切除体 PLY、观察窗详细报告、manifest 和可选局部修正映射；
- 第 7 阶段每个手机区域的摆动包络 PLY 和 `handpiece_avoidance.json`；
- 各阶段排版前的 `raw-overview.png`。

下游程序应读取输出根目录下的阶段 JSON 和最终 STL，不应依赖 `.cache` 路径。

## 三个命令各自产生什么

| 命令 | 文件输出 | 终端输出 |
| --- | --- | --- |
| `process --config CASE_YAML` | 写入七个阶段 JSON、牙位工作流图和计算所需缓存；不实体化最终 STL | 每个阶段的编号、状态、key、成熟度和原因 |
| `generate --config CASE_YAML` | 阶段 JSON/PNG、最终 STL、四张标准视图和内部缓存 | `MODEL` 路径和所有返回的 `IMAGE` 路径 |
| `validate --config CASE_YAML --model MODEL` | 不写文件 | 逐项输出“通过/失败、检查名、指标字典”，任一失败时退出码非零 |

`generate --validate` 在生成后立即执行同样的最终 STL 检查，验证结果仍输出到终端。
具体检查项和阈值见 {doc}`validation`。
