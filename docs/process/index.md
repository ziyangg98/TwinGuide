# 生成过程

当前程序从传统模板定位圆环恢复每个种植位的轴线和止停位置，并按病例 YAML 的
全局标准参数与种植位三项高度覆盖直接生成成对导柱，随后执行 FDI 牙位映射，
在导板上切出导孔和窗口，为每个导管选择高低 Q/P 及基于牙位射线的导板锚点，
生成四根连续曲线梁；还可按配置生成 Y 型按压梁、两分量跨接梁、末端 U 型延伸梁、
末端远中公共节点和手机避让切除。导管、导板和梁架整体融合后，统一复切导孔和
观察窗，再完成 STL 导出。

源码以 `GenerationContext` 传递阶段结果。各阶段页面分别说明算法公式、
分支条件、输出和质量检查；[《TwinGuide 建模架构与实现边界》](../guide/technical-modeling-workflow.md)
说明模块分层和整体布尔顺序。

## 统一阶段输出

`generate` 将阶段产物写入输出根目录：七个阶段均记录 JSON，
完成的阶段同时生成 PNG。

- `stage-NN-<name>.json`：结构化结果，顶层固定为 `schema_version`、
  `stage`、`case`、`inputs`、`parameters`、`result`、`quality`和
  `artifacts`。
- `stage-NN-<name>.png`：由该阶段的几何计划或实体化结果生成的结果图。

| 阶段 | 文件主名 |
| --- | --- |
| 1 | `stage-01-sleeve-reconstruction` |
| 2 | `stage-02-tooth-mapping` |
| 3 | `stage-03-cutout-planning` |
| 4 | `stage-04-anchor-selection` |
| 5 | `stage-05-press-beam` |
| 6 | `stage-06-structure-linking` |
| 7 | `stage-07-clearance-adjustment` |

`twin_guide.stl` 和 `guide_iso/top/bottom/side.png` 是整体建模结果。
可再生成的内部文件按阶段写入 `.cache/stage-NN-<name>/`。

## 算法流程

| 步骤 | 最小几何逻辑 |
| --- | --- |
| 1. 导柱 | 识别传统模板定位圆环；按全局共用的形状与间距参数、每个种植位的轴向规划和三项高度生成相向双柱 |
| 2. 牙位 | 提取物理牙冠核心，以实测外轮廓凹点弦分区，再按有向牙弓赋予 FDI 并映射导板覆盖 |
| 3. 切口 | 导孔沿导管轴线；操作窗覆盖两导管间的操作结构；观察窗沿 FDI 语义轴向真实导板外壁扫射并通过差集质量门 |
| 4. 锚点 | 在 C 口反向外壁生成 Q/P；从牙位 T 点按配置角度发射射线，取导板外壁出口 A |
| 5. 按压结构 | 按模式选择导管高端、三个牙位点或末端 U 梁点，生成三锚点 Y 及抬高汇合点 |
| 6. 连接 | 单种植位按导管生成高/低路径；多种植位按同侧有序路径连续经过全部导管；按需加入 Y 梁和特殊病例梁 |
| 7. 净距 | 以止挡报告确定手机旋转轴/枢轴，精确合并当前深度左右摆动姿态，最终整体直接差集 |

```{toctree}
:maxdepth: 1

stage-1-sleeves
stage-2-teeth
stage-3-cutouts
stage-4-link-points
stage-5-press-beam
stage-6-linking
stage-7-clearance
special-topologies
```
