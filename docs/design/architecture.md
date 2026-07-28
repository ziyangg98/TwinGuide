# 总体架构

TwinGuide 按公开接口、配置、阶段编排、几何计划、Blender 实体化和独立验证分层。

## 公开接口

顶层 `twin_guide` 只导出 `CaseConfig`、`generate_guide()`、
`run_generation_process()`、`validate_guide()` 及对应结果类型。

## 配置与编排

`twin_guide.config` 从一份病例 YAML 生成不可变 `CaseConfig`。七阶段控制器按顺序
调用阶段入口，将类型化结果和运行状态写入 `GenerationContext`。

## 几何层

牙位识别、观察窗、锚点、按压梁和连续连接梁分别返回不依赖最终 Blender 对象的
计划结果。阶段之间只传递公开结果类型，不调用其他阶段的私有函数。观察窗使用
FDI 轴扫算法，连接结构使用连续框架算法，运行时不进行策略分派。

## Blender 层

`twin_guide.blender` 消费几何计划，负责 STL 读写、切割体、曲线扫掠、布尔运算、
体素融合、导孔复切、渲染和导出。该层不得重新读取病例语义或选择算法。

## 验证层

`validate_guide()` 使用相同配置和几何计划重建检查基准，再独立检查最终 STL 的拓扑、
导管保留、连接梁、按压梁、导孔和观察窗。验证不修改待检查模型。
