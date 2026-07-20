# 生成过程

当前程序识别并重建两个导套，在导板上切出导孔和窗口，
为每个导套选择两个导套侧锚点和两个导板侧锚点，生成八条曲线连接管，
复切固定孔后完成实体融合与 STL 导出。

源码以 `GenerationContext` 传递已接入的阶段结果。导孔、操作窗和连接管已有具体几何构造；
观察缺口的位置由前牙牙位确定；当前病例临时使用前牙中线的估计坐标。

牙位识别、按压结构和净距调整尚未接入。它们在运行记录中为 `skipped`，
相应页面保留目标接口和占位图。

```{toctree}
:maxdepth: 1

stage-1-sleeves
stage-2-teeth
stage-3-cutouts
stage-4-link-points
stage-5-press-beam
stage-6-linking
stage-7-clearance
```
