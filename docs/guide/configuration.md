# 病例配置

`CaseConfig.from_json()` 读取 JSON 配置，拒绝未知字段、非法数值和缺失的必填项。
相对路径以配置文件所在目录为基准。

## 配置结构

| 分组 | 内容 |
| --- | --- |
| `case_id` | 病例标识符 |
| `inputs` | 三个病例 STL 路径 |
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
    "template": "../../data/inputs/ring-guide.stl",
    "guide_sleeve_assembly": "../../data/inputs/guide-sleeve.stl",
    "patient_dentition": "../../data/inputs/patient-dentition.stl"
  },
  "geometry": {
    "template_channel_radius_mm": 3.05,
    "channel_axial_margin_mm": 5.0,
    "connector_radius_mm": 1.2,
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
      "mesh": "../../data/inputs/handpiece.stl",
      "head_crop_radius_mm": 10.0,
      "minimum_clearance_mm": 1.0,
      "maximum_tilt_degrees": 5.0,
      "withdrawal_distances_mm": [0.0, 4.0, 8.0, 12.0]
    }
  },
  "output_directory": "../output/twin_guide"
}
```

不包含 `validation` 的配置仍可用于 `generate` 和 `process`，但执行 `validate` 时会报告缺少检查配置。
各几何参数的算法含义见对应的[生成步骤](../process/index.md)。
