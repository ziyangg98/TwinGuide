# 病例配置

`case.yaml` 是一个病例的唯一配置入口。`CaseConfig.from_yaml()` 负责读取
文件、解析路径、校验几何参数和特殊拓扑前置条件。完整的非病人示例位于
`examples/case.example.yaml`。

## 读取规则

- YAML 重复键会直接报错；严格运行分组中的未知字段也会报错。
- 网格和报告路径必须是相对路径，以 `case.yaml` 所在目录为基准，且不得越出该目录。
- STL 和手机止挡 JSON 在加载配置时必须已存在。
- 所有长度使用毫米，角度使用度，三维向量使用病例 STL 的世界坐标系。
- `case.id` 只能包含小写字母、数字、`-` 和 `_`，并且以字母或数字开头。
- 表中“必填”指当前解析器要求显式出现；“默认”指字段缺省时实际使用的值。

## 顶层分组

| 分组 | 必填 | 职责 |
| --- | --- | --- |
| `schema_version` | 否 | YAML 文档结构版本 |
| `case` | 是 | 病例标识及显示元数据 |
| `objects` | 是 | 定义牙列和传统模板输入 |
| `runtime` | 是 | 定义直接进入几何计算的数值参数 |
| `anatomy` | 是 | 定义上下颌、FDI 牙位分类和病例方向 |
| `design` | 否 | 定义观察窗、导板锚点、按压梁和特殊结构 |
| `planning` | 否 | 定义操作窗规划；也可保留种植位语义记录 |
| `tooth_recognition` | 否 | 记录通过人工审核的牙冠接触分隔结果 |
| `review` | 否 | 记录各项人工审核状态和备注 |
| `qa` | 否 | 病例质量检查元数据；不直接生成几何 |

## 病例与输入对象

### `case`

| 字段 | 必填/默认 | 含义 |
| --- | --- | --- |
| `case.id` | 必填 | 稳定病例 ID；也决定默认输出目录 `output/<case_id>/` |
| `case.display_name` | 可选 | 报告和人工阅读使用的名称，不参与几何计算 |
| `case.cohort` | 可选 | 如 `single` 或 `multiple` 的分组元数据，不切换算法 |

### `objects`

```yaml
objects:
  dental: {path: input/patient-dentition.stl}
  guide: {path: input/dental-guide.stl}
```

| 字段 | 必填 | 含义与约束 |
| --- | --- | --- |
| `objects.dental.path` | 是 | 患者牙列 STL，用于牙位识别、导板映射和净距判定 |
| `objects.guide.path` | 是 | 带定位圆环的传统模板 STL，也是开孔、开窗和连接的基体 |

`name` 和 `required` 是对象清单元数据。`objects.handpiece` 与
`objects.cutter` 记录来源对象；第 7 阶段的手机输入由
`runtime.handpiece_avoidance` 指定。

## 导柱标准尺寸

`runtime.sleeve` 保存真实标准导柱的 12 项参数。每个
`planning.guide_posts[].sleeve` 只可覆盖总高、平台总高度和 C 口闭合段高度；
其余 9 项在所有种植位使用全局标准值。
同一种植位左右两根导柱共用合并后的完整参数。顶部凹陷的直径和深度必须成对提供。
导柱位姿由传统模板圆环和 `planning.guide_posts` 确定。
完整的定位、单柱构造、双柱放置和导出验证顺序见
{doc}`../process/stage-1-sleeves`。

| 字段 | 单位 | 含义 | 约束 | 种植位调整 |
| --- | --- | --- | --- | --- |
| `inner_diameter_mm` | mm | 标准导孔直径 | $>0$ | 否 |
| `outer_diameter_mm` | mm | 导管主体外径 | $>\text{inner\_diameter}$ | 否 |
| `top_recess_diameter_mm` | mm | 凹槽端同轴锥形环凹陷外径 | 内孔直径与主体外径之间；须与深度同时提供 | 否 |
| `top_recess_depth_mm` | mm | 凹槽端锥形环连续收敛至贯穿孔的轴向深度 | $>0$ 且小于 C 口段高度；须与直径同时提供 | 否 |
| `height_mm` | mm | 导柱轴向总高 | $>0$ | 是 |
| `platform_slot_width_mm` | mm | 平台开槽段的中央直槽宽度；直槽通过浅圆弧连接中心圆孔 | $0<\text{槽宽}<\text{主体外径}$ | 否 |
| `platform_overhang_mm` | mm | 相向内侧平台端面超出主体圆柱外弧的距离 | $\geq0$；默认 0.20 | 否 |
| `platform_height_mm` | mm | 平台从闭合端向凹槽端延伸的轴向长度 | 见下方高度链 | 是 |
| `closed_bore_height_mm` | mm | C 口闭合段从闭合端起算的轴向长度；中心导孔仍贯通 | 见下方高度链 | 是 |
| `inner_arc_angle_degrees` | 度 | C 口段内孔保留圆弧的圆心角 | $180\leq\theta\leq350$，保证 C 口形态和圆滑过渡可可靠离散 | 否 |
| `outer_arc_angle_degrees` | 度 | C 口段外轮廓保留圆弧的圆心角，同时确定外侧 D 面位置 | $0<\theta<360$ | 否 |
| `guide_spacing_mm` | mm | 左右两根导柱相向内侧平台端面之间的净距，即“平台端面净距” | $>0$；标准值 11.50 | 否，仅全局设置 |

单侧平台宽度不是独立输入，而是由主体外径和中央槽宽唯一确定：

$$
w_{\mathrm{platform}}=
\frac{d_{\mathrm{outer}}-w_{\mathrm{slot}}}{2}.
$$

正文将 `guide_spacing_mm` 称为“平台端面净距”；轴心距、双柱外侧总宽和 C 口 D 面净距
均为派生尺寸。双导放置时先把两个相向内侧平台端面置于中点两侧各
`guide_spacing_mm / 2`，再根据平台端面到轴心的距离计算轴心。设外半径为
$R$、平台凸出量为 $e$、外弧角为 $\theta$，则

$$
d_{\mathrm{axis\to platform}}=R+e,\qquad
d_{\mathrm{axis}}=d_{\mathrm{platform}}+2(R+e).
$$

下部 C 口 D 面净距仍由外弧角决定：

$$
d_{\mathrm{C-gap}}=d_{\mathrm{axis}}-
2R\cos\frac{360^\circ-\theta}{2}.
$$

导出验证分别反测 C 口段、平台开槽段、C 口闭合段及双柱总宽，尺寸公差为 0.001 mm。

高度必须同时满足

$$
0 < h_{\mathrm{closed\ bore}} < h_{\mathrm{platform}} < h_{\mathrm{sleeve}}.
$$

## 通用几何参数

### `runtime.geometry`

| 字段 | 必填/默认 | 含义与约束 |
| --- | --- | --- |
| `channel_axial_margin_mm` | 必填 | 导孔切除体在导管轴向两端的附加伸出量，$\geq0$ |
| `connector_diameter_mm` | `4.60` | 连接梁直径，$>0$ |
| `fusion_voxel_size_mm` | 必填 | 网格融合/离散尺度，$>0$ |
| `connector_dental_clearance_mm` | `0.20` | 连接梁与牙列的净距，$\geq0$ |
| `sleeve_stop_clearance_mm` | `2.0` | 高位连接梁外缘到止停台侧稳定外壁边缘的净距，$\geq0$ |
| `sleeve_stop_front_avoidance_mm` | `0.0` | 高位主连接线在正视平面下拉、避开止停台投影的距离，$\geq0$ |
| `connection_blocks` | 全部启用 | 分别控制 `lower_main`、`upper_main` 和 `press_beam` |
| `connector_guide_endpoint` | 见下表 | 连接梁在导板端的渐粗、根部球和贴合脚参数 |

`connector_guide_endpoint` 也被 `runtime.press_beam.guide_endpoint` 复用：

| 字段 | 默认 | 约束/含义 |
| --- | --- | --- |
| `root_radius_factor` | `1.08` | 根部半径相对梁半径的倍数，$\geq1$ |
| `transition_length_mm` | `3.0` | 渐粗段长度，$>0$ |
| `bulb_radius_factor` | `1.08` | 根部球半径倍数，$\geq1$ |
| `bulb_forward_offset_mm` | `0.08` | 根部球沿梁方向偏移，$\geq0$ |
| `foot_major_radius_mm` | `3.0` | 贴合脚长轴半径，$>0$ |
| `foot_minor_radius_mm` | `2.2` | 贴合脚短轴半径，$0<r_{minor}\leq r_{major}$ |
| `foot_peak_height_mm` | `2.55` | 贴合脚峰高，$>0$ |
| `foot_embed_depth_mm` | `0.25` | 贴合脚嵌入导板的深度，$>0$ |

## 操作窗与观察窗通用参数

### `runtime.windows`

| 字段 | 必填/默认 | 含义与约束 |
| --- | --- | --- |
| `operation_tangent_margin_mm` | 必填 | 操作窗沿局部牙弓切向的余量，$\geq0$ |
| `operation_bitangent_margin_mm` | `3.0` | 操作窗沿局部次切向的余量，$\geq0$ |
| `operation_axial_margin_mm` | `channel_axial_margin_mm` | 操作窗沿导管轴的余量，$\geq0$ |
| `operation_front_axial_margin_mm` | `operation_axial_margin_mm` | 操作窗术区侧轴向余量，$\geq0$ |
| `operation_rear_axial_margin_mm` | `operation_axial_margin_mm` | 操作窗后部轴向余量，$\geq0$ |
| `operation_corner_radius_mm` | $\min(1,\max(0.2,m_b))$ | 操作窗圆角半径，$\geq0$ |
| `observation_axis_drop_mm` | `0.2` | FDI 轴扫观察窗公共轴相对高端牙冠顶的下沉量，$>0$ |
| `observation_sweep_angle_degrees` | `90.0` | 默认扫角，$0<\theta\leq180$ |

`planning.operation_windows` 可以用病例语义名称覆盖四个操作窗数值：

| `planning.operation_windows` | 覆盖的 `runtime.windows` |
| --- | --- |
| `tangent_margin_mm` | `operation_tangent_margin_mm` |
| `bitangent_margin_mm` | `operation_bitangent_margin_mm` |
| `axial_margin_mm` | `operation_axial_margin_mm` |
| `front_axial_margin_mm` | `operation_front_axial_margin_mm` |
| `rear_axial_margin_mm` | `operation_rear_axial_margin_mm` |
| `corner_radius_mm` | `operation_corner_radius_mm` |

其余规划字段当前只接受下列固定语义：`mode: per_implant_site`、
`center_mode: paired_sleeve_operation_feature`、
`axis_mode: paired_sleeve_average_axis`、`overlap_rule: union_cutters` 和
`cut_target: guide_template_only`。`sites` 如果出现必须是数组，当前作为种植位对照记录；
窗体数量仍由实际识别的导管对决定。

### `planning.clinical_parameters`

该接口保留 `implant_coordinates_path`、`implant_coordinates_format`、
`extension_mm`、`extension_definition`、`mouth_opening_mm`、
`adapter_length_mm` 和 `height_formula_id`。坐标路径与格式、延长量与定义必须成对提供。
在医生确认坐标格式、延长量定义和高度公式前，这些字段只记录输入，不参与几何计算。

### `planning.guide_posts`

每个已识别圆环分别配置 `ring_index`、`drill_length_mm`、`implant_length_mm`、
`sleeve_template_extension_mm` 和可选的 `sleeve`。模板延伸长度用于从传统模板
止停面恢复植体顶端。`sleeve` 只接受 `height_mm`、`platform_height_mm` 和
`closed_bore_height_mm`，且三个字段均可省略；出现的字段只覆盖当前种植位，
左右两根同步使用。
`ring_index` 对应传统模板圆环识别结果中的编号，不依赖 FDI、sleeve 或 handpiece。
系统按 `drill_length_mm - 12 mm - implant_length_mm` 计算双导导板延长量；12 mm
为钻针位于手机内部的固定长度，不作为病例参数。模板延伸长度与双导延伸长度不要求
相等；两者之差决定新止停平面相对传统模板圆环上平面的轴向偏移。当前示例起始值为
钻针 33 mm、植体 12 mm、模板延伸长度 8 mm，因此计算得到的双导延伸长度为 9 mm；
这三项仍须按种植位显式填写。正式流程要求至少
配置一个 `guide_posts`，不再支持从导管装配体回退识别。

```yaml
planning:
  guide_posts:
    - ring_index: 1
      drill_length_mm: 33.00
      implant_length_mm: 12.00
      sleeve_template_extension_mm: 8.00
      sleeve:
        height_mm: 16.00
        platform_height_mm: 10.50
        closed_bore_height_mm: 5.00
```

三项高度合并后重新执行完整约束校验，并必须满足
`0 < closed_bore_height_mm < platform_height_mm < height_mm`。其余导柱字段出现在
种植位 `sleeve` 中会作为配置错误拒绝。

### `design.observation_windows`

每个元素定义一个 FDI 范围的轴扫观察窗。

| 字段 | 必填/默认 | 含义与约束 |
| --- | --- | --- |
| `id` | 默认 `window` | 观察窗稳定标识 |
| `start_fdi`, `end_fdi` | 必填 | 窗的两个端点牙位；必须是已测量的现存牙 |
| `extent_mode` | `center_to_center` | `center_to_center` 取两牙中心；`full_teeth` 扩展到两牙外边界 |
| `height_mm` | `4.0` | 轮廓采样深度；轴扫切除体的径向范围由导板穿透求交决定 |
| `top_open` | `true` | 是否从导板牙合侧开放 |
| `top_bridge_margin_mm` | `0.5` | `top_open: false` 时保留的顶部连接高度 |
| `opening_side` | `labial_buccal_exterior` | 当前唯一支持的开放侧 |
| `opening_geometry` | 必填 | `axis_sweep` |
| `axis_drop_mm` | `1.0` | 本窗公共轴下沉量，$>0$；与 `runtime.windows.observation_axis_drop_mm` 一致 |
| `sweep_angle_deg` | `90.0` | 本窗扫角，$0<\theta\leq180$；与运行参数一致 |
| `angular_spacing_deg` | `3.0` | 角向采样间距，$>0$ |
| `axis_sections` | 按轴长自动 | 显式轴向采样数，$\geq2$ |
| `angle_sections` | 按扫角自动 | 显式角向采样数，$\geq2$ |
| `requested_sections` | 按牙弓长自动 | 导板轮廓诊断采样数 |

## 牙位与病例方向

### `anatomy`

| 字段 | 必填/默认 | 含义与约束 |
| --- | --- | --- |
| `jaw` | 必填 | `maxillary` 或 `mandibular` |
| `present_teeth` | 必填 | 实际可见并参与牙冠识别的永久牙 FDI |
| `missing_teeth` | 必填 | 牙弓序列中保留语义位置的缺失牙 FDI |
| `excluded_teeth` | 必填 | 不进入本次牙弓序列的 FDI |
| `fdi_order` | 可选 | 显式的患者右→左顺序；必须恰好等于 `present + missing` 且符合标准顺序 |
| `orientation` | 可选 | 人工确认的病例坐标轴；未提供时由牙列和 FDI 语义估计 |
| `review_status` | 可选 | 生产审核状态 |

`present_teeth`、`missing_teeth` 和 `excluded_teeth` 必须无重复、互斥，并完整分类当前颌的
FDI 范围。

```yaml
anatomy:
  jaw: maxillary
  orientation:
    patient_right_to_left_axis: [0.978820, -0.166764, -0.118751]
    anterior_to_posterior_axis: [0.187334, 0.963550, 0.190991]
    occlusal_axis: [-0.082573, 0.209192, -0.974382]
```

三个方向向量是牙位映射的确认坐标框。其中 `occlusal_axis` 还可写为
`+X/-X/+Y/-Y/+Z/-Z`；几何层会归一化该向量。导管平台端方向由导管本身的端部拓扑确定，
`jaw` 和牙合轴提供病例语义及一致性约束，不替代导管端部判定。

### `tooth_recognition`

只有通过人工确认的特定分隔线才应写入：

```yaml
tooth_recognition:
  approved_contact_separators:
    - fdis: [21, 22]
      selection_method: shortest_valid_local_neck_pair
      review_status: user_confirmed
```

`fdis` 必须包含两个不同的整数 FDI；当前可审批的方法只有
`shortest_valid_local_neck_pair`；`review_status` 必须为 `user_confirmed`；同一
FDI 对不得重复。

## 导板锚点

### `design.guide_anchors`

| `mode` | 端点数 | 用途 |
| --- | ---: | --- |
| `nearest` | 0 | 从导管锚点就近寻找导板连接点 |
| `tooth_section_trajectory` | 2 | 单种植位的两个牙位轨迹端点 |
| `adjacent_two_implant_continuous_paths` | 2 | 相邻双种植位的两组连续路径 |
| `terminal_distal_common_node` | 1 | 单个末端远中公共节点 |
| `adjacent_two_implant_terminal_distal_node_paths` | 1 | 相邻双种植位到末端公共节点的路径 |

病例配置使用 `anchors`，每个端点必须恰好各有一个 `u_side` 和
`back_u_side` 锚点：

```yaml
design:
  guide_anchors:
    mode: tooth_section_trajectory
    anchors:
      - id: mesial_u
        endpoint: mesial
        side: u_side
        station: {type: tooth_center, fdi: 13}
        ray_angle_degrees: 70.0
      - id: mesial_back_u
        endpoint: mesial
        side: back_u_side
        station: {type: tooth_center, fdi: 13}
        ray_angle_degrees: 90.0
      # 第二个 endpoint 同样配置 U/背 U 两个锚点
```

| 锚点字段 | 含义与约束 |
| --- | --- |
| `id` | 唯一锚点 ID，使用与 `case.id` 相同的字符规则 |
| `endpoint` | 将 U/背 U 两个锚点归入同一导板端 |
| `side` | `u_side` 或 `back_u_side` |
| `station` | `{type: tooth_center, fdi: N}` 或 `{type: tooth_pair_midpoint, fdis: [N, N]}` |
| `ray_angle_degrees` | 从站位局部参考轴起算的射线角，$0<\theta\leq180$ |

两个末端公共节点模式还必须配置 `terminal_distal_common_node`：

| 字段 | 必填/默认 | 约束 |
| --- | --- | --- |
| `missing_fdi` | 必填 | 末端缺牙 FDI，必须属于 `anatomy.missing_teeth` |
| `reference_neighbor_fdi` | 必填 | 现存的直接近中参考牙 |
| `implant_fdis` | `[]` | 双种植位末端模式必须是两个连续远中缺牙位 |
| `node_radius_factor` | `1.12` | 公共节点相对连接梁半径的倍数，$\geq1$ |
| `distal_offset_sleeve_diameters` | `2.0` | 节点远中偏移，当前固定为 2 个平均导管外径 |

## 按压梁

`design.press_beam` 定义拓扑和牙位锚点；`runtime.press_beam` 可放置纯几何尺寸。
两分组会合并为一份参数，同名字段不得在两处重复。

| `mode` | `stations` | 其他要求 |
| --- | ---: | --- |
| `disabled` | 0 | 不生成按压梁 |
| `inner_sleeve_upper_y` | 2 | 从每个种植位的内侧导管高端选一个 Y 型汇合点 |
| `three_tooth_anchors_y` | 3 | 三个牙位锚点形成 Y 型梁 |
| `terminal_u_extension_anchor_y` | 2 | 两个牙位锚点与末端 U 延伸梁上的第三锚点汇合 |

每个 `stations` 元素使用上节的 `tooth_center` 或 `tooth_pair_midpoint`，并且
必须显式配置 `ray_angle_degrees`。

| 字段 | 默认 | 含义与约束 |
| --- | --- | --- |
| `diameter_mm` | `4.60` | 按压梁直径，$>0$ |
| `guide_overlap_mm` | `0.30` | 按压梁在导板端的嵌入量，$0\leq d<\text{diameter}/2$ |
| `junction_sleeve_distance_mm` | `6.0` | Y 型汇合点与导管锚点的目标距离，$>0$ |
| `junction_axial_lift_mm` | `2.0` | 汇合点沿导管轴向的抬升量，$>0$ |
| `minimum_junction_angle_degrees` | `25.0` | 两分支最小夹角，$0<\theta\leq180$ |
| `guide_endpoint` | 通用默认 | 按压梁导板端的渐粗和贴合脚，字段同 `connector_guide_endpoint` |

`inner_sleeve_upper_y.sleeve_anchor_selection` 可显式写出当前唯一策略：

```yaml
sleeve_anchor_selection:
  candidate_scope: inner_sleeve_upper_per_implant_site
  distance_score: maximin_to_two_guide_anchors
  tie_breaker: larger_sum_distance
```

`terminal_u_extension_anchor_y.extension_anchor` 的字段为：`segment` 取
`u_side/back_u_side/turnaround/full`；`selection` 固定为
`farthest_from_guide_anchors`；`start_margin_mm` 默认 `4.6`，`end_margin_mm`
默认 `0.0`，`overlap_mm` 默认 `0.30` 且必须小于按压梁半径。

## 特殊导板结构

### `design.guide_component_bridge`

用于在第 4 阶段前连接恰好两个断开的导板分量。

| 字段 | 默认 | 要求 |
| --- | --- | --- |
| `enabled` | `false` | 必须与 `mode` 是否为 `disabled` 一致 |
| `mode` | 随 `enabled` | `disabled` 或 `same_side_dual_beam` |
| `required_guide_component_count` | `2` | 启用时固定为 2 |
| `stations` | `[]` | 启用时恰好两个唯一 ID 站位；每项必须给出 U/背 U 射线角 |
| `connection_rule` | `same_side` | 当前固定为同侧相连 |
| `require_different_guide_components` | `true` | 两端是否必须落在不同导板分量 |
| `diameter_mm` | `4.60` | 桥接梁直径，$>0$ |
| `dental_clearance_mm` | `0.20` | 牙列净距，$\geq0$ |
| `endpoint_reinforcement.enabled` | `false` | 启用导板端加强 |
| `endpoint_reinforcement.method` | `bulb_and_conformal_foot` | 启用时的唯一方法 |

### `design.guide_terminal_u_extension`

用于从导板末端两侧锚点绕过末端牙形成 U 型延伸梁。

| 字段 | 默认 | 要求 |
| --- | --- | --- |
| `enabled` | `false` | 必须与 `mode` 一致 |
| `mode` | 随 `enabled` | `disabled` 或 `tooth_wrapping_u_beam` |
| `anchor_station` | 无 | 启用时必填，使用单牙中心或双牙中点站位 |
| `u_side_ray_angle_degrees` | `70.0` | U 侧射线角，$(0,180]$ |
| `back_u_side_ray_angle_degrees` | `90.0` | 背 U 侧射线角，$(0,180]$ |
| `terminal_fdi` | 无 | 启用时必填；必须是现存末端牙 |
| `reference_neighbor_fdi` | 无 | 启用时必填；必须是直接近中现存牙 |
| `diameter_mm` | `4.60` | U 型梁直径，$>0$ |
| `dental_clearance_mm` | `0.20` | 牙列净距，$\geq0$ |
| `safety_margin_mm` | `0.30` | 额外安全余量，$\geq0$ |
| `turnaround_depth_mm` | `3.0` | 回转深度，不得小于 U 型梁半径 |
| `endpoint_reinforcement` | 同上 | 只支持 `bulb_and_conformal_foot` |

`guide_terminal_u_extension` 与 `guide_anchors.terminal_distal_common_node` 不得同时启用。

## 手机避让

`runtime.handpiece_avoidance` 可写为单个对象或对象数组。每项独立构造当前装配深度下的
左右摆动包络，并在第 7 阶段从完整模型中差集。

| 字段 | 必填/默认 | 含义与约束 |
| --- | --- | --- |
| `id` | `handpiece_N` | 本避让区域唯一 ID，使用病例 ID 字符规则 |
| `handpiece` | 必填 | 与病例同坐标系的手机 STL |
| `stop_report` | 必填 | 与该手机配对的止挡 JSON |
| `maximum_angle_degrees` | `5.0` | 左右最大摆角，$0<\theta\leq45$ |
| `pose_samples` | `41` | 等角度姿态数；必须是不小于 3 的奇数，以包含 $0^\circ$ |
| `union_batch_size` | `7` | 包络布尔并集批量，$\geq2$ |
| `extra_clearance_mm` | `0.0` | 对包络追加的净距，$\geq0$ |

## 渲染与输出位置

`runtime.render.width_px` 和 `height_px` 全部必填，且必须是正整数。它们控制
Blender 阶段结果图和最终标准视图的像素尺寸。

默认输出目录是代码仓库的 `output/<case.id>/`。只有
`generate --output DIRECTORY` 会对本次生成覆盖该目录；它不会回写 `case.yaml`。
输出文件和 JSON 字段的详细含义见 {doc}`outputs`。

## 图形编辑器覆盖值

`editor_overrides` 由 Blender 面板管理。导柱参数的生效顺序为：`runtime.sleeve`
全局标准值、`planning.guide_posts[].sleeve` 三项种植位高度覆盖、`sleeve_sites`
三项高度调整。
`sleeve_sites` 按 `ring_index` 保存总高、平台总高度和 C 口闭合段高度，一组控制点
同步作用于该种植位左右两根导柱。其余分组为 `operation_windows`、
`observation_windows`、`connector_avoidance`、`surface_anchors` 和
`press_junction_mm`。

```yaml
editor_overrides:
  sleeve_sites:
    - ring_index: 1
      height_mm: 16.00
      platform_height_mm: 10.50
      closed_bore_height_mm: 4.90
```

旧式 `sleeve_guides` 仅在同一种植位的两个 `guide_index` 同时存在，且三项高度各自
保留两位小数后的结果完全一致时迁移为 `sleeve_sites`；迁移直接采用该共同显示值，
编辑器再次保存时写出新格式。

`connector_avoidance.path_fraction` 保持旧字段兼容，其含义是从止停台接触点到
所选侧路线端点的比例；`downward_offset_mm` 是正视方向的下拉偏移。

## 审核状态

`generate` 会检查：

- `anatomy.review_status`；
- `review` 下所有名称以 `_status` 结尾的字段。

任一值为 `pending`、`pending_user_input` 或 `unreviewed` 时，生产生成停止。
`--allow-unreviewed` 对本次 `generate` 放行；`process` 和 `validate`
不执行审核检查。`review.notes` 记录人工说明。

## 规划记录

以下字段记录病例规划和审核信息，不直接进入几何计算：

- `planning.implant_sites`：FDI、圆环序号和手机的人工对照表；
- `planning.connector_frame`：连接拓扑的设计记录；当前拓扑由 `guide_anchors`、按压梁和特殊结构的类型化参数决定；
- `design.tube_opening`、`design.reinforcement`、`design.handpiece_motion`：设计记录；
- `qa`：病例质量检查备注或预期值。

几何计算使用本页前述的运行字段。
