# 运行程序

## 环境

TwinGuide 支持 Python 3.13–3.14。Blender 建模和集成测试使用 Blender 5.2 LTS。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
blender --version
```

## 生成 STL

```bash
blender -b --python run.py -- generate --config examples/case.json
```

`generate` 读取病例网格，完成导套重建、导孔与窗口规划、联建锚点选择和
八条曲线连接管生成，再执行实体化、布尔运算、固定孔复切、网格清理和 STL 导出。
观察缺口的位置由前牙牙位确定，当前病例使用临时估计坐标；
按压结构和净距调整不在本次生成中。
具体数据流见[生成过程](../process/index.md)。

## 查看计算过程

```bash
blender -b --python run.py -- process --config examples/case.json
```

`process` 在命令行输出所有已声明阶段的运行状态。导套重建、切口规划、
联建锚点选择和曲线连接为 `completed`，预留扩展为 `skipped`。
该命令计算几何结果，但不执行 STL 实体化与导出。

## 检查 STL

```bash
blender -b --python run.py -- validate \
  --config examples/case.json \
  --model output/twin_guide/twin_guide.stl
```

## 输出文件

| 文件 | 内容 |
| --- | --- |
| `twin_guide.stl` | 导出的牙科导板 |
| `input_template.png` | 牙科导板输入网格 |
| `input_sleeves.png` | 导套装配体输入网格 |
| `input_patient_dentition.png` | 患者牙列输入网格 |
| `reconstructed_sleeves.png` | 导套参数化重建 |
| `guide_assembly.png` | 牙科导板、重建导套和保留附件的装配关系 |
| `cutouts.png` | 导孔、操作窗和前牙开放观察缺口 |
| `link_points.png` | 导套侧和牙科导板侧锚点 |
| `guide_connectors.png` | 曲线连接管 |
| `guide_*.png` | 最终网格的标准视图 |
