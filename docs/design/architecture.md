# 总体架构

程序按职责分为公开接口、生成控制、几何计算、Blender 建模和结果检查。
病例网格与配置由公开接口读取，生成控制组织已接入的几何计算，
Blender 建模层将几何计划转换为网格并导出 STL，检查层再以导出文件和病例配置为基准计算质量指标。

## 公开接口

`twin_guide` 导出病例配置、各生成阶段的几何接口、生成入口和结果检查入口。
使用者无需通过内部模块路径导入公开类型。
第 2 步执行牙位识别和观察窗构造，第 5 步可生成 Y 型按压梁，第 7 步可生成
手机当前深度左右摆动包络。

## 生成控制

`run_generation_process()` 执行导管识别与实体模式选择、可选牙位工作流、切口规划、
联建锚点选择、曲线连接和可选手机包络构造，将输出写入 `GenerationContext`。
`generate_guide()` 随后调用 Blender 建模层。

`twin_guide.strategies` 是融合层：观察窗和连接梁分别通过稳定分派接口选择
当前 TwinGuide 实现或 TwinGuideMerge 兼容实现。选择只来自
`case.yaml design.algorithms`，后续实体化和验证消费同一份策略结果，避免生成与
检查使用不同算法。

## 几何计算

病例分析负责读取 Blender 网格并建立公用的 `CaseAnalysis`。
导孔与窗口规划、锚点选择和连接曲线计算使用显式数据类传递几何结果。
牙位识别和导板映射的完整实现位于 `twin_guide.tooth_mapping`，观察窗实体算法
位于 `twin_guide.observation_window_engine`；两者均为项目内部模块，不从上级
工作区或历史 `scripts` 目录导入业务函数。

## Blender 建模

`twin_guide.blender` 负责 STL 读写、网格查询、标准导管重建或输入导管保护性融合、
切割体创建、曲线管实体化、模式化固定孔处理、体素融合、渲染和导出。
`generated` 模式整体融合后复切导孔并保留最大连通体；`input` 模式先复切不含
输入导管的基础体，再融合真实导管并只清除不超过单体素量级的数值碎片。

## 结果检查

`validate_guide()` 以导出 STL 为检查对象，使用病例配置重建检查基准，
返回各项 `ValidationResult`。

## 运行时依赖边界

`blender-env.sh` 只将项目内 `.blender-site-packages`、`src/` 和项目根目录加入
`PYTHONPATH`，不会继承调用者已有的 Python 搜索路径。第三方数值和网格库由
`pyproject.toml` 声明，并由 `requirements-blender.lock.txt` 锁定；TwinGuide
业务算法不要求项目目录之外存在任何 Python 包或脚本。
