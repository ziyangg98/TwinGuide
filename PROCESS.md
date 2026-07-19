# 七阶段生成流程

| 阶段 | 公开函数 | 状态 | 输入与输出 |
| --- | --- | --- | --- |
| 1. 导套识别与生成 | `twin_guide.recognize_and_build_sleeves` | 稳定 | 导套装配体与牙科导板定位信息 → 导套参数、闭合重建和质量诊断 |
| 2. 牙位识别 | 待定 | 待实现 | 尚未实现 |
| 3. 操作窗和观察窗切口 | `twin_guide.plan_window_cutouts` | 实验 | 牙科导板分析与第 1 步结果 → 通道与窗口计划 |
| 4. 导套与牙科导板联建选点 | `twin_guide.select_template_link_points` | 实验 | 第 1、3 步结果 → 导套侧和牙科导板侧选点 |
| 5. 按压梁柱选点 | 待定 | 待实现 | 尚未实现 |
| 6. 选点连接 | `twin_guide.link_selected_points` | 实验 | 第 4 步结果 → 导套—牙科导板平滑连接计划 |
| 7. 避让空间调整 | 待定 | 待实现 | 尚未实现 |

## 运行

```bash
blender -b --python run.py -- process --config examples/case.json
```

```python
from twin_guide import run_generation_process

result = run_generation_process(config)
for stage in result.stages:
    print(stage.definition.number, stage.status, stage.reason)
```

`generate_guide()` 将第 1、3、4、6 步的结果实体化并导出 STL。
