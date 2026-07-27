# 7. 避让空间调整

**实现状态：实验性 0.1。** 病例配置 `handpiece_avoidance` 时执行；未配置时
记录为 `skipped`。

## 功能

在每个已配置手机的当前装配深度下，读取手机 STL 与止挡分析报告。旋转轴采用报告中的
双导管 `pair_axis`，枢轴采用左右匹配手机止挡面中心的中点。手机最大面积
连通分量绕该轴从负角度扫到正角度，默认 `-5°～+5°` 共 41 个姿态，不做
轴向下压。所有姿态用 manifold3d 分批精确并集为封闭运动包络。

包络按输入 SHA-256 与运动参数缓存。最终导板完成导管、梁架融合以及固定孔、
观察窗复切后，按配置顺序直接扣除全部包络；梁架不设保护区，进入包络的部分同样会被切掉。
`extra_clearance_mm` 默认为 `0.0`，因此默认不对包络额外膨胀。

## 结果示例

单个手机时，第 7 步在输出目录的 `handpiece_avoidance/` 中保存；多个手机时，
按 `handpiece_avoidance/<id>/` 分目录保存：

- `handpiece_current_depth_lr_sweep_envelope.ply`：精确扫掠包络；
- `handpiece_avoidance.json`：轴、枢轴、姿态角、采样误差和缓存指纹。
