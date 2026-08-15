# 运行程序

## 环境

TwinGuide 使用 Homebrew 安装的 Blender 5.2 LTS 及其 Python 3.13。科学计算依赖
按锁定版本安装到项目内的 `.blender-site-packages`，不修改 Blender 应用。

```bash
brew install --cask blender
./scripts/setup.sh
./scripts/blender.sh --background --factory-startup \
  --python scripts/verify_environment.py
```

依赖的精确版本记录在 `requirements-blender.lock.txt`。后续命令统一通过
`./twinguide` 运行。
## 生成 STL

```bash
./twinguide generate --config ../data/cases/case-3af68d1cfda4/case.yaml
```

`generate` 读取病例网格和 `case.yaml`，依次完成传统模板导柱定位与按种植位参数重建、
牙位识别与导板映射、FDI 观察窗规划、锚点选择、连续连接梁和配置的按压梁。
随后程序融合全部结构，复切导孔和观察窗；配置手机避让时，再执行运动包络差集，最后导出 STL。
具体数据流见[生成过程](../process/index.md)。

输入和几何指纹未变化时，`generate` 直接复用完整正式产物。使用 `--force`
可忽略计算缓存并完整重建；`process --force` 对七阶段规划采用相同语义。

`generate` 默认拒绝 `case.yaml` 中标记为
`pending_user_input`、`pending` 或 `unreviewed` 的病例。审核检查适用于 `generate`；
`process` 和 `validate` 可用于诊断。需要在未审核状态下临时生成时，必须显式加上
`--allow-unreviewed`。

每个病例使用一份 `case.yaml`，手机避让和其他阶段参数均在其中配置。

## 图形化微调

```bash
./twinguide ui --config ../data/cases/case-3af68d1cfda4/case.yaml
```

启动后会加载病例模型和结构列表。选择操作窗、观察窗、连接线、支撑结构或导柱，
即可通过三维手柄或右侧输入框调整参数。

操作窗边缘成对移动且中心移动不改变宽高；观察窗端点逐牙吸附有效 FDI；左右
每根导柱左右两侧的连接线避让节点彼此独立，且不能拖入平台的自动禁入区；表面图钉通过鼠标射线沿导板或牙面移动；每个种植位置
显示一组三个导柱高度控制环，沿该位置轴线移动时左右导柱同步更新，并在相邻高度前
停止。拖动时按 `Shift` 以 0.1 mm（角度为
1°）精调，`Esc` 取消当前拖动。

“保存调整”写回 `case.yaml`；“更新实体预览”重新生成当前模型；“确认导出并检验”
生成正式模型并执行几何检验。待审核病例可以编辑和预览，命令行正式生成仍执行审核
检查。

编辑器启动一个病例专用后台 worker。热更新按结构编号复用牙位、窗口、已切导板和
单根连接梁检查点；操作窗、双侧连接避让和支撑结构只重建其依赖项。快速预览保持
0.2 mm 体素精度，正式导出仍使用完整修复与检验流程。

需要在生成后立即验收最终 STL 时，可使用：

```bash
./twinguide generate \
  --config ../data/cases/case-67d787b33006/case.yaml --validate
```

`--validate` 在生成后调用与 `validate` 子命令相同的检查器。检查器按同一份配置
重新执行七阶段计算以建立验证基准；第 2 阶段可在输入指纹完全一致时复用缓存，
其余阶段计划会重新计算。随后检查器独立读取最终 STL；
任一检查失败时命令返回非零状态。对末端 U 型延伸梁和末端远中公共节点这类
特殊拓扑模式，该选项同时检查对应结构。

## 查看计算过程

```bash
./twinguide process --config ../data/cases/case-3af68d1cfda4/case.yaml
```

`process` 计算七个阶段的几何结果并输出运行状态，但不执行最终 STL 的
实体化与导出。配置手机避让时，第 7 阶段会计算对应的运动包络计划。

## 检查 STL

```bash
./twinguide validate \
  --config ../data/cases/case-3af68d1cfda4/case.yaml \
  --model output/tooth_11/twin_guide.stl
```

`validate` 检查拓扑、导管、连接梁、按压梁、导孔、操作窗、观察窗和特殊结构。
手机包络差集由 `generate` 执行，`validate` 检查差集后的最终模型，
不把包络重新应用到待检查模型。由于验证基准来自 `run_generation_process()`，
命令仍可能复用或刷新第 2 阶段缓存，并重写病例默认输出目录中的七阶段 JSON；
它不会修改 `--model` 指向的 STL。检查项和阈值见 {doc}`validation`。

## 开发检查

Blender 后端与端到端测试、全部病例回归分别使用：

```bash
./scripts/blender.sh --background --python scripts/run_tests.py
.venv/bin/python scripts/run_all_cases.py --force
```

全病例脚本自动发现 `data/cases/case-*/case.yaml`，逐例生成并验证，单例失败不会
阻断后续病例。结果写入 `output/all-cases/summary.json`，可视化汇总写入
`output/all-cases/report.html`；缺少 `case.yaml` 的数据目录单独列为未配置，不计为生成失败。

## 新病例落地顺序

1. 准备同一世界坐标系的牙列和带定位圆环的传统模板。
2. 在 `case.yaml` 中完成上下颌、现存/缺失/排除牙、每个圆环的导柱规划参数、标准导柱尺寸和观察窗。
3. 确认 FDI 右→左顺序、缺失种植位和必要的病例坐标轴。
4. 根据导板拓扑选择普通主梁、两分量桥接、末端 U 延伸或远中公共节点，并配置相应锚点。
5. 按需选择一种 Y 型按压梁和一个或多个手机避让区域。
6. 先运行 `process`，依次检查牙位、观察窗、Q/P、导板射线锚点、按压梁汇合点和特殊结构方向。
7. 完成人工审核状态后运行 `generate --validate`，同时审核最终 STL、七阶段 JSON/PNG 和逐项验证指标。

特殊病例的前置条件和几何定义见 {doc}`../process/special-topologies`。

## 输出文件

最终 STL、四个标准视图、七阶段 JSON/PNG、各阶段指标以及
`.cache` 内部产物的完整契约见 {doc}`outputs`。

已有全病例回归的逐例最终图和指标见 {doc}`case-results`。
