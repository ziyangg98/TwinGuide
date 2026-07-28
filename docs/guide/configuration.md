# 病例配置

`CaseConfig.from_yaml()` 读取一份完整的 `case.yaml`。程序不再读取病例 JSON，
也不再合并多个配置来源。

## 顶层结构

| 分组 | 职责 |
| --- | --- |
| `schema_version` | 配置格式版本 |
| `case` | 病例 ID、显示名称和队列 |
| `objects` | 患者牙列、牙科导板、导管装配体和手机文件 |
| `runtime` | 导管尺寸、网格参数、窗口参数、手机避让和渲染参数 |
| `anatomy` | 上下颌、FDI 牙位、方向轴和人工审核状态 |
| `design` | 导管模式、观察窗、锚点、按压梁和特殊结构 |
| `planning` | 多种植位拓扑及操作窗关系 |
| `review` | 对象映射和各设计步骤的审核状态 |

所有输入路径以 `case.yaml` 所在目录为基准。路径必须留在当前病例目录中；患者
来源文件不得复制到代码仓库。

## 对象

```yaml
objects:
  dental: {path: input/patient-dentition.stl}
  guide: {path: input/dental-guide.stl}
  sleeve:
    files:
      - {id: sleeve_01, path: input/sleeve-assembly.stl}
    active_ids: [sleeve_01]
```

单种植位启用一个导管装配体；多种植位按种植位分别保存和启用，不在数据整理阶段
合并 STL。

## 运行参数

`runtime.sleeve` 保存导管的八个几何尺寸；`runtime.geometry` 保存导孔轴向余量、
连接梁直径、牙列净距和体素分辨率；`runtime.windows` 保存操作窗与 FDI 轴扫观察窗
参数；`runtime.render` 保存诊断图尺寸。

启用手机避让时，每个对象必须同时提供手机 STL 和止挡报告：

```yaml
runtime:
  handpiece_avoidance:
    - id: handpiece_01
      handpiece: input/handpiece.stl
      stop_report: input/handpiece-stop-01.json
      maximum_angle_degrees: 5.0
      pose_samples: 41
      union_batch_size: 7
      extra_clearance_mm: 0.0
```

`pose_samples` 必须为大于等于 3 的奇数。

## 解剖与设计

`anatomy.jaw` 只接受 `maxillary` 或 `mandibular`。`present_teeth`、
`missing_teeth` 和 `excluded_teeth` 必须互斥并使用合法 FDI 编码。人工提供的方向轴
必须是有限非零三维向量。

当前只有一套生产算法：FDI 轴扫观察窗和连续连接梁。因此配置中不存在算法 profile
或旧策略开关；未知的 `design.algorithms` 会被拒绝。

`design.sleeve_geometry.mode` 可选：

- `input`：保留识别出的真实输入导管；
- `generated`：使用识别位姿和 `runtime.sleeve` 尺寸重建标准导管。

`design.guide_anchors.anchors` 为每个导板端锚点显式声明 `endpoint`、牙位
`station`、`side` 和 `ray_angle_degrees`。按压梁、末端 U 型延伸、跨组件桥接和
远中公共节点只有在对应字段完整时才启用，缺失前置条件会直接报错。

## 输出和审核

默认输出目录为代码仓库 `output/<case_id>/`。CLI 的 `generate --output` 可以为一次
运行覆盖该目录，但不会修改 `case.yaml`。

若 `anatomy.review_status` 或 `review.*_status` 为 `pending`、
`pending_user_input`、`unreviewed`，正式生成会停止。只有诊断运行可以显式使用
`--allow-unreviewed`。

完整的非患者模板见 `examples/case.example.yaml`。
