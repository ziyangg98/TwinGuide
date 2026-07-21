# 病例配置

`CaseConfig.from_json()` 读取并校验 JSON 配置。相对路径以配置文件所在目录为基准。

## 配置结构

| 分组 | 内容 |
| --- | --- |
| `case_id` | 病例标识符 |
| `jaw` | 上下颌：`upper` 或 `lower`；用于观察窗牙合侧定向 |
| `inputs` | 三个病例 STL 路径 |
| `sleeve` | 导柱的八个几何参数 |
| `geometry` | 导孔、连接管和体素融合参数 |
| `windows` | 操作窗扩展余量 |
| `render` | 过程图和结果图的像素尺寸 |
| `output_directory` | STL 和过程图的输出目录 |

## 完整示例

```json
{
  "case_id": "tooth_47",
  "jaw": "lower",
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
  "output_directory": "../output/tooth_47"
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

`sleeve` 集中定义导柱的八个标量尺寸，`geometry.connector_diameter_mm`
定义连接柱直径。两个 C 口分别指向对侧导柱。
角度在配置中使用度，建模时转换为弧度。

## 当前观察缺口约定

| 参数 | 当前值 | 含义 |
| --- | ---: | --- |
| 局部位置 | 牙面对应点 | 导板前缘中线附近的最近牙面 |
| 缺口宽度 | 7.0 mm | 观察缺口沿牙弓横向的宽度 |
| 缺口下缘 | 牙面高度 | 刚好露出牙齿 |
| 切入深度 | 局部厚度 + 1.6 mm | 覆盖导板并在两侧各留 0.8 mm 余量 |

观察缺口的位置和下缘由导板与患者牙列 STL 的几何对应确定。

牙科手机净距尚未实现，当前配置不包含相关字段。
各几何参数的算法含义见对应的[生成步骤](../process/index.md)。
