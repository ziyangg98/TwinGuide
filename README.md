# TwinGuide

TwinGuide 面向双导套牙科导板的自动化建模，负责输入导管识别、导孔与窗口规划、
结构连接、Blender 实体化、STL 导出和独立检查。

## 问题描述

已有牙科导板提供与患者牙列匹配的基体，导套装配体给出两个导套及其操作结构的
位置和尺寸。需要在不改变装配关系的前提下，在牙科导板上形成与导套同轴的导孔，
开设操作窗和观察窗，并用平滑、连通的结构将导套与牙科导板连接为一体。

完整目标还包括识别牙位、构建按压梁柱，以及根据患者牙列、牙科手机运动包络和必须保留的
功能区调整连接结构。最终模型应保持导孔、导套固定孔和窗口通畅，各结构互相连通，
并满足所需净距，输出可供后续制造和检查的一体化牙科导板 STL。

程序使用以下三个病例网格：

<table>
  <tr>
    <td width="33.3%" align="center"><img src="docs/images/input-template.png" width="100%" alt="牙科导板"></td>
    <td width="33.3%" align="center"><img src="docs/images/input-sleeves.png" width="100%" alt="导套装配体"></td>
    <td width="33.3%" align="center"><img src="docs/images/input-patient-dentition.png" width="100%" alt="患者牙列扫描"></td>
  </tr>
  <tr>
    <td align="center">牙科导板</td>
    <td align="center">导套装配体</td>
    <td align="center">患者牙列扫描</td>
  </tr>
</table>

输入网格的坐标、单位和网格质量要求见使用指南中的“输入数据”页。

> **当前程序：** 程序从输入装配体识别具有真实轴孔的导管，并按病例 YAML 选择
> 参数化重建标准导管（`generated`）或直接保留输入导管（`input`），随后规划导孔、操作窗和观察窗，
> 为每个导套选择上下 Q/P 和导板左右 A/S 锚点，生成共四根连续曲线梁，
> 并输出 `twin_guide.stl`。配置已审核报告时，第 2 步读取 FDI 牙位，
> 第 3 步在 TwinGuide 内生成 0.2 mm/90° 轴扫掠观察窗；局部检查失败时
> 依次尝试 0.5、1.0、2.0 mm。无牙位映射时不生成观察窗。
> 可选生成 Y 型按压梁；配置手机 STL 与止挡报告时，第 7 步生成当前深度
> `-5°～+5°` 左右摆动包络，并在最终整体上直接切除避障空间。

## 功能与结果

<table>
  <tr>
    <td width="50%" align="center"><img src="docs/images/sleeve-reconstruction.png" width="100%" alt="导管识别与实体模式选择"></td>
    <td width="50%" align="center"><img src="docs/images/tooth-identification-placeholder.svg" width="100%" alt="牙位识别与导板映射示意"></td>
  </tr>
  <tr><td align="center">1. 导管识别与实体模式选择</td><td align="center">2. 牙位识别与导板映射</td></tr>
  <tr>
    <td align="center"><img src="docs/images/cutout-planning.png" width="100%" alt="导孔与窗口规划"></td>
    <td align="center"><img src="docs/images/link-point-selection.png" width="100%" alt="联建锚点选择"></td>
  </tr>
  <tr><td align="center">3. 导孔与窗口规划</td><td align="center">4. 导套与牙科导板联建锚点选择</td></tr>
  <tr>
    <td align="center"><img src="docs/images/press-beam-placeholder.svg" width="100%" alt="按压梁柱锚点选择示意"></td>
    <td align="center"><img src="docs/images/point-linking.png" width="100%" alt="光滑连接管生成"></td>
  </tr>
  <tr><td align="center">5. 按压梁柱锚点选择</td><td align="center">6. 四根连续曲线梁生成与模式化固定孔处理</td></tr>
  <tr>
    <td align="center"><img src="docs/images/clearance-adjustment-placeholder.svg" width="100%" alt="手机运动包络避让"></td>
    <td align="center"><img src="docs/images/output-twin-guide.png" width="100%" alt="牙科导板 STL"></td>
  </tr>
  <tr><td align="center">7. 手机当前深度左右摆动避让</td><td align="center">当前版本输出的牙科导板 STL</td></tr>
</table>

## 安装与运行

TwinGuide 支持 Python 3.13–3.14，网格建模使用 Blender 5.2 LTS。
标准环境使用 Blender 5.2 自带的 Python 3.13 和项目局部依赖目录，
不会修改 Blender 应用内的 `site-packages`。

```bash
./setup-blender-env.sh
./blender-env.sh --background --factory-startup --python verify-blender-env.py

./blender-env.sh --background --python run.py -- \
  generate --config examples/case-tooth-11.json
```

依赖版本锁定在 `requirements-blender.lock.txt`；`blender-env.sh` 会启用
Blender 的 system-env 模式并且只加载项目内 `.blender-site-packages` 与 TwinGuide 源码；
牙位识别、导板映射和观察窗算法均已包含在本项目中。

示例配置统一命名为 `examples/case-tooth-<牙号>.json`，生成结果写入
`output/tooth_<牙号>`。
规范病例数据位于 `data/cases/single/tooth-<FDI>`，每个目录包含
`case.yaml` 和使用统一语义文件名的 `input/`；目录规范见
[`data/cases/single/README.md`](data/cases/single/README.md)。
当前病例的导柱内径为 2.10 mm、主体外径为 4.30 mm、连接梁默认直径为 4.60 mm；
完整参数见使用指南的“病例配置”页。

<!-- sphinx-homepage-end -->

## 文档

- [使用指南](https://ziyangg98.github.io/TwinGuide/guide/index.html)
- [生成过程](https://ziyangg98.github.io/TwinGuide/process/index.html)
- [程序设计](https://ziyangg98.github.io/TwinGuide/design/index.html)
- [Python API 参考](https://ziyangg98.github.io/TwinGuide/public_api.html)
