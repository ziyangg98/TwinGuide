# 结果检查

`validate_guide()` 读取已导出的 STL，并按当前病例配置重新建立检查基准，返回
一组 `ValidationResult`。每项结果包含检查名称、是否通过及数值指标；检查函数
不修改最终模型。

| 检查项 | 检查内容 |
| --- | --- |
| `topology` | 边界边、非流形边、重复三角形、连通分量数和最小分量顶点数 |
| `guide_retention` | 当前模式导管表面位于最终实体内部或距表面不超过两个融合体素的比例 |
| `guide_connectors` | 主连接梁中心线的包覆率、导管接触距离和导板锚点距离 |
| `connector_endpoint_reinforcement` | 每个唯一导板端的根部球和曲面贴合脚覆盖率 |
| `press_beam` | 已配置 Y 梁三臂、汇合点和导板端强化的保留情况 |
| `channels` | 每个导孔多截面、多半径探针是否被最终实体阻塞 |
| `observation_windows` | 每个 FDI 观察窗的样本净空和局部阻塞数量 |

导管保留率通过阈值为 0.90。`generated` 模式以参数化重建导管为参考；`input`
模式以实际选中的输入组件为参考，并额外记录每根导管的源组件编号和轴向孔道
通过率。被导板或连接梁包入实体内部的导管表面仍计为保留。

`channels` 除阻塞数量外还记录：

- `input_geometry_preserved`：最终模型是否采用受保护输入导管；
- `global_bore_recut_applied`：是否对生成导管整体执行全局导孔复切；
- `connector_bore_recut_before_input_sleeves`：是否只在输入导管加入前清除基础体侵入；
- `blocked_input_geometry_requires_source_bore_review`：输入导管自身孔道是否需要人工复核。

输入导管模式不会为了通过检查而重新钻孔。若真实输入导管本身堵塞，验证明确失败；
生成导管模式则允许融合后全局复切标准孔道。导孔检查在奇偶射线与最近表面法向
侧别冲突时，以孔腔侧局部表面方向排除三角交线误投票。

手机包络差集由 `generate` 执行；当前 `validate` 不重新计算运动包络。
