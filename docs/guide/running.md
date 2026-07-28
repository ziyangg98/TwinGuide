# 运行程序

## 环境

TwinGuide 使用 Blender 5.2 LTS 自带的 Python 3.13。科学计算依赖安装到项目内的
`.blender-site-packages`，不修改 Blender 应用本身。

```bash
./scripts/setup.sh
./scripts/blender.sh --background --factory-startup \
  --python scripts/verify_environment.py
```

依赖的精确版本记录在 `requirements-blender.lock.txt`。后续命令统一通过
`./twinguide` 运行。底层 `scripts/blender.sh` 只把项目内依赖目录、
`src/` 和项目根目录加入
`PYTHONPATH`；牙位识别、导板映射和观察窗算法均从 TwinGuide 内部加载。
## 生成 STL

```bash
./twinguide generate --config ../data/cases/single/tooth-11/case.yaml
```

`generate` 读取病例网格和 `case.yaml` 人工牙位约束，现场完成牙位识别与导板映射、
导管装配体识别与位姿分析，再按 `runtime.sleeve` 的标准尺寸重建导管，
FDI 变截面观察窗、联建锚点选择、连续曲线梁和可选 Y 型按压梁生成，
再执行融合、固定孔/观察窗复切以及可选的手机运动包络直接差集，最后导出 STL。
具体数据流见[生成过程](../process/index.md)。

`generate` 默认拒绝 `case.yaml` 中仍明确标记为
`pending_user_input`、`pending` 或 `unreviewed` 的病例。这个安全门只影响生产生成；
`process` 和 `validate` 仍可用于诊断。需要在未审核状态下临时生成时，必须显式加上
`--allow-unreviewed`。

每个病例只保留一个 `case.yaml` 正式配置；该配置统一接入
`case.yaml` 和对应手机避让，不再维护无手机避让或仅执行部分阶段的重复 JSON。

需要在生成后立即验收最终 STL 时，可使用：

```bash
./twinguide generate \
  --config ../data/cases/single/tooth-14/case.yaml --validate
```

`--validate` 复用本次生成的中间语义并对最终 STL 执行独立几何检查；
任一检查失败时命令返回非零状态。对末端 U 型延伸梁和末端远中公共节点这类
特殊拓扑模式，建议使用该选项。

## 查看计算过程

```bash
./twinguide process --config ../data/cases/single/tooth-11/case.yaml
```

`process` 在命令行输出所有已声明阶段的运行状态。集成配置中，
可选步骤按配置显示为 `completed` 或 `skipped`；配置手机避障时第 7 步
会生成或复用运动包络，但 `process` 不实体化最终导板。
该命令计算几何结果，但不执行 STL 实体化与导出。

## 检查 STL

```bash
./twinguide validate \
  --config ../data/cases/single/tooth-11/case.yaml \
  --model output/tooth_11/twin_guide.stl
```

## 开发检查

Blender 后端与端到端测试、全部病例回归分别使用：

```bash
./scripts/blender.sh --background --python scripts/run_tests.py
./scripts/blender.sh --background --python scripts/run_regression.py
```

`validate` 当前检查拓扑、导管保留、连接管、导孔和窗口。手机包络差集在
`generate` 中执行；`validate` 不重复构造包络。

## 输出文件

| 文件 | 内容 |
| --- | --- |
| `twin_guide.stl` | 导出的牙科导板 |
| `input_template.png` | 牙科导板输入网格 |
| `input_sleeves.png` | 导管装配体输入网格 |
| `input_patient_dentition.png` | 患者牙列输入网格 |
| `generated_sleeves.png` | 按识别位姿和配置参数重建的标准导管 |
| `guide_assembly.png` | 牙科导板与标准导管的装配关系 |
| `cutouts.png` | 导孔、操作窗和 FDI 变截面观察窗 |
| `link_points.png` | 导管侧和牙科导板侧锚点 |
| `press_beam.png` | 按压梁轨迹与实体计划 |
| `guide_connectors.png` | 四根连续曲线梁 |
| `handpiece_avoidance.png` | 手机摆动包络与当前几何计划 |
| `guide_*.png` | 最终网格的标准视图 |
| `handpiece_avoidance/[<id>/]handpiece_current_depth_lr_sweep_envelope.ply` | 手机左右摆动包络；多手机时按编号分目录 |
| `handpiece_avoidance/[<id>/]handpiece_avoidance.json` | 手机避障运动模型与缓存报告；多手机时按编号分目录 |
