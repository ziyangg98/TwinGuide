# 1. 从传统模板生成导柱

**实现状态：稳定。** 正式流程不再读取导管装配体 STL。每个种植位只根据传统模板上的圆环、已确认的病例方向和 `planning.guide_posts` 参数生成一对标准导柱。

```text
模板圆环识别 → 圆环上平面与轴线估计 → 植体顶端定位
→ 双导止停台定位 → D 面方向估计 → 标准导柱重建
```

## 输入

- `objects.guide.path`：包含定位圆环的传统模板 STL。
- `planning.guide_posts[].ring_index`：该种植位对应的圆环序号。
- `drill_length_mm`、`implant_length_mm`：用于计算双导导板延长量。
- `sleeve_template_extension_mm`：圆环上平面到植体顶端的轴向距离。
- `runtime.sleeve`：导柱截面、高度及两根导柱的 D 面净距。

双导导板延长量为

$$
L_{\mathrm{twin}}=L_{\mathrm{drill}}-12\ \mathrm{mm}-L_{\mathrm{implant}}.
$$

圆环上平面中心记为 $\mathbf c_r$，朝向牙合外侧的单位轴记为
$\mathbf a$。植体顶端和止停台中心依次为

$$
\mathbf c_i=\mathbf c_r-L_{\mathrm{template}}\mathbf a,
\qquad
\mathbf c_s=\mathbf c_i+L_{\mathrm{twin}}\mathbf a.
$$

## 双导间距

`guide_spacing_mm` 是两根导柱相向内侧 D 面之间的净距，不是轴心距。
程序根据标准外径和外弧角计算轴心到 D 面的偏移量，再反推出轴心距，保证生成截面的 D 面净距严格等于配置值。

## 输出

每个圆环输出两根 `GuideSleeve` 和一个操作区域特征。后续开孔、选点、连接及手机干涉检查只消费这些标准重建结果，不存在从导管装配体 STL 回退识别的分支。

## 代码对应

- 圆环、上平面和 D 面方向估计：`template_ring_estimation.py`。
- 延长量计算：`guide_post_positioning.py`。
- 病例导入与标准导柱生成：`case_analysis.py` 的 `_build_template_only_guides()`。
