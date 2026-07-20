# 病例配置

`CaseConfig.from_json()` 读取并校验 JSON 配置。相对路径以配置文件所在目录为基准。

## 配置结构

| 分组 | 内容 |
| --- | --- |
| `case_id` | 病例标识符 |
| `inputs` | 三个病例 STL 路径 |
| `sleeve` | 导柱的八个几何参数 |
| `geometry` | 导孔、连接管和体素融合参数 |
| `windows` | 操作窗扩展余量 |
| `render` | 过程图和结果图的像素尺寸 |
| `validation` | 可选；牙科手机、净距和运动采样参数，执行 `validate` 时必需 |
| `output_directory` | STL 和过程图的输出目录 |

## 完整示例

```json
{
  "case_id": "case_r305_h500",
  "inputs": {
    "template": "../../data/cases/single/tooth-47/input/ring-guide.stl",
    "guide_sleeve_assembly": "../../data/cases/single/tooth-47/input/guide-sleeve-47-34-10-12-s-40s.stl",
    "patient_dentition": "../../data/cases/single/tooth-47/input/lbk.stl"
  },
  "sleeve": {
    "inner_diameter_mm": 2.10,
    "outer_diameter_mm": 4.3,
    "height_mm": 16.373,
    "platform_width_mm": 2.036,
    "platform_height_mm": 9.875,
    "closed_bore_height_mm": 4.777,
    "inner_arc_angle_degrees": 264.934,
    "outer_arc_angle_degrees": 211.684
  },
  "geometry": {
    "channel_axial_margin_mm": 5.0,
    "connector_diameter_mm": 2.3,
    "fusion_voxel_size_mm": 0.2
  },
  "windows": {
    "operation_tangent_margin_mm": 1.0,
    "operation_bitangent_margin_mm": 0.5
  },
  "render": {
    "width_px": 1600,
    "height_px": 1200
  },
  "validation": {
    "handpiece": {
      "mesh": "../../data/cases/single/tooth-47/input/handpiece-47.stl",
      "head_crop_radius_mm": 10.0,
      "minimum_clearance_mm": 1.0,
      "maximum_tilt_degrees": 5.0,
      "withdrawal_distances_mm": [0.0, 4.0, 8.0, 12.0]
    }
  },
  "output_directory": "../output/twin_guide"
}
```

## 导柱参数

![导柱参数示意](../images/sleeve-parameters.png)

| 参数 | 当前值 | 含义 |
| --- | ---: | --- |
| $2R_{\mathrm{in}}$ | 2.10 mm | 导柱内径 |
| $2R_{\mathrm{out}}$ | 4.30 mm | 导柱主体外径 |
| $H$ | 16.373 mm | 导柱总高度 |
| $W_{\mathrm p}$ | 2.036 mm | 平台径向长度 |
| $h_{\mathrm p}$ | 9.875 mm | 平台段高度 |
| $h_{\mathrm s}$ | 4.777 mm | 闭合孔段高度 |
| $\phi_{\mathrm{in}}$ | 264.934° | 内圆弧覆盖角 |
| $\phi_{\mathrm{out}}$ | 211.684° | 主体外圆弧覆盖角 |
| 连接柱直径 | 2.30 mm | 导柱与导板之间的连接柱直径 |

`sleeve` 集中定义导柱形状，`geometry.connector_diameter_mm` 定义连接柱直径。
角度在配置中使用度，建模时转换为弧度。

## 当前观察缺口参数

| 参数 | 当前值 | 含义 |
| --- | ---: | --- |
| 局部横向坐标 | 0.0 mm | 前牙中线的当前估计位置 |
| 局部前后向坐标 | 22.4 mm | 前牙区的当前估计位置 |
| 缺口宽度 | 7.0 mm | 观察缺口沿牙弓横向的宽度 |
| 切入深度 | 5.5 mm | 观察缺口向导板内的深度 |

观察缺口的位置由前牙牙位确定，表中坐标是当前病例的临时估计值。

`validation` 用于独立检查；`generate` 和 `process` 只使用建模参数。
各几何参数的算法含义见对应的[生成步骤](../process/index.md)。
