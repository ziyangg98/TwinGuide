# 5. 按压梁锚点与汇合点

**实现状态：实验。** 本阶段只生成 `PressBeamPointPlan`：三个中心线锚点、
一个 Y 形汇合点以及用于第 6 阶段实体化的轨迹。它不生成网格，
也不在多个候选结构中自动挑选外形。

## 输入与符号

| 符号 | 含义 |
| --- | --- |
| $\mathbf C_i$ | 第 $i$ 根导管中心 |
| $\mathbf H_j,\mathbf o_j$ | 第 $j$ 颗牙的牙冠点与局部牙弓外向 |
| $\mathbf A_k,\mathbf n_k$ | 射线命中的导板外壁点与外法向 |
| $r,o$ | 按压梁半径与预埋量 |
| $\mathbf S_k$ | 由外壁点换算的中心线锚点 |
| $\mathbf J$ | Y 形汇合点 |

第 4 阶段的导管端锚点直接复用已验证的高端 $\mathbf P$；
导板端则从外壁点向外法向偏移：

$$
\mathbf S_k=\mathbf A_k+(r-o)\mathbf n_k.
$$

这使半径为 $r$ 的梁在导板中实际预埋 $o$，而不是只在外壁相切。

## 1. 导板锚点的唯一射线

每个站位由 YAML 明确给出单牙中心或双牙中点，记相应牙冠参考点为
$\mathbf H_k$。以病例确认的牙合向 $\mathbf e_{occ}$ 向内偏置 $d_T=2$ mm：

$$
\mathbf T_k=\mathbf H_k-d_T\mathbf e_{occ}.
$$

将局部牙弓切向 $\mathbf t_k$ 投影到 $\mathbf e_{occ}$ 的法平面：

$$
\widehat{\mathbf t}_k=
\operatorname{unit}\!\left[
\mathbf t_k-(\mathbf t_k\cdot\mathbf e_{occ})\mathbf e_{occ}
\right].
$$

在 $\operatorname{span}(\mathbf e_{occ},\widehat{\mathbf t}_k)$ 内，按 YAML 角度
$\alpha_k$ 朝 U 型牙弓内侧旋转得单位射线 $\mathbf d_k$。外向
$\mathbf o_k$ 只用于确定旋转符号。导板外壁锚点是

$$
\mathbf A_k=
\operatorname*{arg\,min}_{\mathbf x\in\mathcal I_k}
\|\mathbf x-\mathbf T_k\|,
\qquad
\mathcal I_k=
\left\{
\mathbf T_k+\lambda\mathbf d_k:\lambda>0,
\ \mathbf n(\mathbf x)\cdot\mathbf d_k>0
\right\}.
$$

即只取第一个法向朝射线的导板外壁出口。不生成多条轨迹，
不用弧长百分比猜锚点。

## 2. 模式 A：内侧导管高端 Y 梁

对每个种植位内的两根导管，找到各自最近牙位 $j(i)$，并计算

$$
s_i=(\mathbf C_i-\mathbf H_{j(i)})\cdot\mathbf o_{j(i)}.
$$

$s_i$ 较小者是该种植位的牙弓内侧导管；两根的分数差必须至少
0.50 mm。多种植位时，每个种植位先各保留一个内侧候选，再用

$$
\left(
\min_k\|\mathbf P_i-\mathbf S_k\|,
\sum_k\|\mathbf P_i-\mathbf S_k\|
\right)
$$

按字典序取最大，使导管端不偏贴任一导板端。

令三个锚点为 $(\mathbf P,\mathbf S_1,\mathbf S_2)$，其几何中位点为

$$
\mathbf g=
\operatorname*{arg\,min}_{\mathbf x}
\sum_{k=0}^{2}\|\mathbf x-\mathbf p_k\|.
$$

代码用 Weiszfeld 迭代求解，距离分母下限为 $10^{-8}$ mm，
最多迭代 128 次，更新位移不超过 $10^{-7}$ mm 时收敛：

$$
\mathbf x^{(m+1)}=
\frac{\sum_k \mathbf p_k/\|\mathbf x^{(m)}-\mathbf p_k\|}
{\sum_k 1/\|\mathbf x^{(m)}-\mathbf p_k\|}.
$$

导管正轴 $\mathbf a$ 由低端 $\mathbf P^-$ 指向高端 $\mathbf P$。令
$h=(\mathbf g-\mathbf P)\cdot\mathbf a$，初始汇合点为

$$
\mathbf J_0=
\begin{cases}
\mathbf g+\ell\mathbf a, & h>10^{-8}\ \mathrm{mm},\\
\mathbf g-h\mathbf a, & h\le10^{-8}\ \mathrm{mm},
\end{cases}
$$

其中 $\ell$ 是 `junction_axial_lift_mm`。第一分支在中位点之上继续
抬高；第二分支只把它投影到经过 $\mathbf P$ 的轴向等高平面。

若 $\|\mathbf J_0-\mathbf P\|<d_{min}$，保持轴向坐标不变，只扩大径向分量。
记

$$
h_J=(\mathbf J_0-\mathbf P)\cdot\mathbf a,\qquad
\mathbf r_J=\mathbf J_0-\mathbf P-h_J\mathbf a,
$$

则

$$
\mathbf J=
\mathbf P+h_J\mathbf a+
\operatorname{unit}(\mathbf r_J)
\sqrt{\max(0,d_{min}^2-h_J^2)}.
$$

当已满足距离时直接取 $\mathbf J=\mathbf J_0$。默认 $d_{min}=6$ mm；
它是下限，不是强制固定距离。
若 $\|\mathbf r_J\|\le10^{-8}$ mm，代码改用两个导板锚点均值在该轴向平面内的投影；
该方向仍退化时失败。

## 3. 模式 B：三导板锚点 Y 梁

三个锚点全部由第 1 节的固定射线得到。汇合点是算术中心沿牙合轴
抬高：

$$
\overline{\mathbf S}=\frac{\mathbf S_1+\mathbf S_2+\mathbf S_3}{3},
\qquad
\mathbf J=\overline{\mathbf S}+\ell\mathbf e_{occ}.
$$

此模式不使用导管轴，也不应用 6 mm 导管距离约束。三锚点必须满足

$$
\|(\mathbf S_2-\mathbf S_1)\times(\mathbf S_3-\mathbf S_1)\|>2r^2,
\qquad
\min_{i<j}\|\mathbf S_i-\mathbf S_j\|>2r,
$$

以避免三条梁在几何上无法展开。

## 4. 模式 C：末端 U 型延伸锚点 Y 梁

两个导板锚点为 $\mathbf S_1,\mathbf S_2$，U 型延伸梁的允许中心线段为
$\Gamma$。扣除配置的首尾余量后，第三锚点是

$$
\mathbf S_3=
\operatorname*{arg\,max}_{\mathbf x\in\Gamma}
\min\left(
\|\mathbf x-\mathbf S_1\|,
\|\mathbf x-\mathbf S_2\|
\right).
$$

若多点具有同一最小距离，再最大化两距离之和。代码在每条折线段上只检查
两端以及到两锚点距离相等的内部点，因为分段内 maximin 的最大值只可能
出现在这些位置。随后用模式 B 的牙合抬高规则生成 $\mathbf J$。

## 5. 共通质量约束

对三个中心线锚点 $\mathbf p_i$，汇合点处的单位臂方向为

$$
\mathbf u_i=\frac{\mathbf p_i-\mathbf J}{\|\mathbf p_i-\mathbf J\|}.
$$

最小展开角为

$$
\theta_{min}=\min_{i<j}
\arccos\!\left[
\operatorname{clip}(\mathbf u_i\cdot\mathbf u_j,-1,1)
\right].
$$

必须满足 $\theta_{min}\ge\theta_{cfg}$，默认
$\theta_{cfg}=25^\circ$。第 6 阶段将三条 $\mathbf p_i\rightarrow\mathbf J$ 直线
扫掠为圆梁，并在 $\mathbf J$ 放置半径 $1.12r$ 的汇合球。

## 输出与失败条件

`stage-05-press-beam.json` 记录模式、三锚点、汇合点、射线角、
距离和夹角检查；`stage-05-press-beam.png` 显示真实导板、锚点、
汇合点与三条轨迹。

以下情形直接失败：射线无有效外壁出口、内外导管分数差不足、
径向方向退化、三锚点无法展开、最小夹角不足或导管最小距离不满足。
程序不用自动更换模式或扩大容差规避失败。

## 代码对应

| 文档算法 | 当前代码入口 |
| --- | --- |
| U 侧固定射线和外壁出口 | `tooth_section_anchors.select_tooth_section_u_side_ray_anchors` |
| 内侧导管评分与候选选择 | `press_beam_points._inner_sleeve_scores`、`select_press_beam_points` |
| 几何中位点与条件汇合点 | `_geometric_median`、`_conditional_inner_sleeve_junction` |
| 三导板锚点汇合 | `_lifted_three_anchor_junction` |
| U 型延伸梁最远锚点 | `_farthest_point_from_two_anchors` |

![Y 型按压梁锚点和汇合点](../images/stage-5-press-beam.png)

*tooth-11 完整运行的第 5 阶段结果。金色线为三个锚点到 Y 型汇合点的计划路径；应同时核对汇合角、导管距离和导板端位置。*
