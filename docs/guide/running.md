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
./twinguide generate --config ../data/cases/single/tooth-11/case.yaml
```

`generate` 读取病例网格和 `case.yaml`，依次完成导管识别与标准重建、
牙位识别与导板映射、FDI 观察窗规划、锚点选择、连续连接梁和配置的按压梁。
随后程序融合全部结构，复切导孔和观察窗；配置手机避让时，再执行运动包络差集，最后导出 STL。
具体数据流见[生成过程](../process/index.md)。

`generate` 默认拒绝 `case.yaml` 中标记为
`pending_user_input`、`pending` 或 `unreviewed` 的病例。审核检查适用于 `generate`；
`process` 和 `validate` 可用于诊断。需要在未审核状态下临时生成时，必须显式加上
`--allow-unreviewed`。

每个病例使用一份 `case.yaml`，手机避让和其他阶段参数均在其中配置。

需要在生成后立即验收最终 STL 时，可使用：

```bash
./twinguide generate \
  --config ../data/cases/single/tooth-14/case.yaml --validate
```

`--validate` 复用本次生成的中间语义并对最终 STL 执行独立几何检查；
任一检查失败时命令返回非零状态。对末端 U 型延伸梁和末端远中公共节点这类
特殊拓扑模式，该选项同时检查对应结构。

## 查看计算过程

```bash
./twinguide process --config ../data/cases/single/tooth-11/case.yaml
```

`process` 计算七个阶段的几何结果并输出运行状态，但不执行最终 STL 的
实体化与导出。配置手机避让时，第 7 阶段会计算对应的运动包络计划。

## 检查 STL

```bash
./twinguide validate \
  --config ../data/cases/single/tooth-11/case.yaml \
  --model output/tooth_11/twin_guide.stl
```

`validate` 检查拓扑、导管、连接梁、按压梁、导孔、观察窗和特殊结构。
手机包络差集由 `generate` 执行，`validate` 检查差集后的最终模型，
不重新构造运动包络。检查项和阈值见 {doc}`validation`。

## 开发检查

Blender 后端与端到端测试、全部病例回归分别使用：

```bash
./scripts/blender.sh --background --python scripts/run_tests.py
./scripts/blender.sh --background --python scripts/run_regression.py
```

## 新病例落地顺序

1. 准备同一世界坐标系的牙列、导板和逐种植位导管装配体。
2. 在 `case.yaml` 中完成上下颌、现存/缺失/排除牙、标准导管尺寸和观察窗。
3. 确认 FDI 右→左顺序、缺失种植位和必要的病例坐标轴。
4. 根据导板拓扑选择普通主梁、两分量桥接、末端 U 延伸或远中公共节点，并配置相应锚点。
5. 按需选择一种 Y 型按压梁和一个或多个手机避让区域。
6. 先运行 `process`，依次检查牙位、观察窗、Q/P、导板射线锚点、按压梁汇合点和特殊结构方向。
7. 完成人工审核状态后运行 `generate --validate`，同时审核最终 STL、七阶段 JSON/PNG 和逐项验证指标。

特殊病例的前置条件和几何定义见 {doc}`../process/special-topologies`。

## 输出文件

最终 STL、四个标准视图、七阶段 JSON/PNG、各阶段指标以及
`.cache` 内部产物的完整契约见 {doc}`outputs`。
