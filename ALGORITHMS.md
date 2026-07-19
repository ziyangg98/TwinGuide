# 第 4、6 步函数逻辑说明

本文档说明导套—牙科导板联建中已实现函数的具体执行逻辑。
接口类型和字段见 [`API.md`](API.md)，阶段依赖见 [`ARCHITECTURE.md`](ARCHITECTURE.md)。

## 1. 总体数据流

```text
CaseAnalysis + SleeveGenerationResult + CutoutPlan
                         |
                         v
             select_sleeve_anchors()
                         |
                         v
             select_template_points()
                         |
                         v
          select_template_link_points()
                         |
                         v
              link_selected_points()
                         |
                         v
             create_point_link_meshes()
                         |
                         v
              build_guide_from_links()
```

第 4 步结束时只有点和诊断。第 6 步才生成贝塞尔中心线；
Blender 建模层才将中心线转换为曲线管网格。

## 2. `select_sleeve_anchors()`

### 2.1 导套侧径向

`_body_wall_direction()` 将平台方向投影到导套轴线的法平面，再取反向：

$$
\mathbf f_i
=
-\frac{\mathbf e_i-(\mathbf e_i^\mathsf T\mathbf a_i)\mathbf a_i}
{\left\|\mathbf e_i-(\mathbf e_i^\mathsf T\mathbf a_i)\mathbf a_i\right\|}.
$$

如果投影长度不超过方向容差，则无法定义径向，两个锚点都标记为不可行。

### 2.2 轴向高度和外壁交点

对每个导套使用固定高度比例：

$$
\eta_i^-=z_{i,\min}+0.25H_i,
\qquad
\eta_i^+=z_{i,\min}+0.75H_i.
$$

截面中心和候选外壁点为

$$
\mathbf c_i^\alpha=\mathbf c_i+\eta_i^\alpha\mathbf a_i,
\qquad
\mathbf s_i^\alpha=\mathbf c_i^\alpha+r_i\mathbf f_i.
$$

`_is_exposed_body_wall()` 根据轴向高度区分主体圆弧段和平台段：

1. 主体圆弧段使用开口半角判断候选方向是否落在保留外圆弧上。
2. 平台段只接受平台反侧的半圆外壁。
3. 不满足条件时保留截面中心和失败原因，但不返回伪造位置。

## 3. `select_template_points()`

### 3.1 清理牙科导板表面样本

`_remaining_template_samples()` 依次检查每个牙科导板表面样本：

1. 计算到所有有向窗口长方体的有符号距离。
2. 计算到所有有限圆柱通道的有符号距离。
3. 只保留与每个切口的距离都不小于 `template_clearance_mm` 的样本。

该处使用第 4 步独立净距，不读取第 6 步连接管半径。

### 3.2 左右方向

导套上下锚点的中点为

$$
\mathbf m_i=\frac{\mathbf s_i^-+\mathbf s_i^+}{2}.
$$

左右分组方向为

$$
\mathbf l_i
=
\frac{\mathbf a_i\times\mathbf f_i}
{\|\mathbf a_i\times\mathbf f_i\|}.
$$

样本满足 $(\mathbf x-\mathbf m_i)^\mathsf T\mathbf l_i<0$ 时进入左侧集合，
大于零时进入右侧集合。这保证两点分居导套两侧。

### 3.3 候选排序与成对评分

1. 按样本到 $\mathbf m_i$ 的距离升序排列，面索引作为确定性次序。
2. 只保留前 `surface_sample_limit` 个样本。
3. 左右侧分别保留前 `candidate_limit` 个候选点。
4. 枚举左右组合，丢弃间距小于
   $\max(r_i,\texttt{minimum\_template\_span\_mm})$ 的点对。
5. 按下列字典序选择最小者：

```text
(左点到中点距离 + 右点到中点距离,
 左右点间距,
 左点面索引,
 右点面索引)
```

因此首先尽可能接近导套，在总距离相同时选择更紧凑的可行点对。

## 4. `select_template_link_points()`

该函数是第 4 步的组合器，不实现新的几何算法：

1. 检查 `context.case.guide_sleeves` 与 `context.sleeves.sleeves` 一致。
2. 调用 `select_sleeve_anchors()`。
3. 将导套锚点传给 `select_template_points()`。
4. 按导套汇总两侧可行性和第一个失败原因。
5. 返回不含中心线的 `TemplateLinkPointPlan`。

## 5. `link_selected_points()`

### 5.1 连接拓扑

每个导套使用两个导套锚点和两个牙科导板点，构造完全二部图：

```text
lower -> left
lower -> right
upper -> left
upper -> right
```

任一侧选点不可行时立即抛出 `ValueError`，不进入部分建模。

### 5.2 三次贝塞尔曲线

设起点和终点为 $\mathbf p_0,\mathbf p_3$，距离为 $d$，连接半径为 $r$。
控制柄长度为

$$
h=\min\left(\frac d3,\kappa r\right),
$$

其中 $\kappa$ 为 `handle_factor`。第一个控制点沿导套径向伸出：

$$
\mathbf p_1=\mathbf p_0+h\mathbf f_i.
$$

牙科导板法向先调整到朝向导套的半空间，再定义

$$
\mathbf p_2=\mathbf p_3+h\mathbf n.
$$

曲线为

$$
\gamma(t)
=(1-t)^3\mathbf p_0
+3(1-t)^2t\mathbf p_1
+3(1-t)t^2\mathbf p_2
+t^3\mathbf p_3.
$$

诊断中心线样本数为

$$
N=\max\left(16,
\left\lfloor\frac d{\max(0.35r,0.15)}\right\rfloor+1
\right).
$$

## 6. Blender 实体化

`create_point_link_meshes()` 对每条 `PointLink` 执行：

1. 创建只含两个贝塞尔节点的 Blender 三维曲线。
2. 将两个内控制点设为节点右柄和左柄。
3. 使用 `radius_mm` 作为曲线倒角深度，并启用圆形端盖。
4. 将曲线转为网格。
5. `recut_sleeve_bore=True` 时，用对应导套的固定孔圆柱复切该连接管。

`build_guide_from_links()` 再将已切窗牙科导板、参数化导套、保留附件和八条连接管
做一次体素融合，清理小连通分量后导出 STL。

## 7. 失败分支

| 条件 | 处理 |
| --- | --- |
| 导套径向无法定义 | 锚点标记不可行，保留原因 |
| 径向射线落入开口或平台非圆弧区 | 对应锚点标记不可行 |
| 切口净距后没有牙科导板样本 | 牙科导板点选择失败 |
| 只在导套一侧找到候选点 | 不生成同侧替代点对 |
| 没有点对满足最小间距 | 牙科导板点选择失败 |
| 第 6 步收到不可行选点 | 抛出 `ValueError`，停止生成 |
| 未完成的第 2、5、7 步 | 记录 `skipped`，对应输出保持为 `None` |
