# 4. 导管与导板锚点选择

**实现状态：实验。** 本阶段只选点，不构造连接梁。对每根标准导管生成
高、低两组导管侧 $Q/P$ 点，并从已切窗导板的真实外壁选择两个
$A$ 点。输出是 `TemplateLinkPointPlan`，第 6 阶段只消费这些类型化锚点。

## 输入与符号

| 符号 | 含义 |
| --- | --- |
| $\mathbf C_i,\mathbf a_i$ | 第 $i$ 根导管中心与有符号轴 |
| $\mathbf d_i^C$ | 导管 C 口方向 |
| $r_s,r_b,r_c$ | 导管外半径、导孔半径、连接梁半径 |
| $[t_i^-,t_i^+]$ | 标准导管轴向范围 |
| $\mathbf H_j,\mathbf o_j$ | 第 $j$ 个牙位的牙冠点和局部外向 |
| $\mathbf e_{occ}$ | 第 2 阶段确认的牙合外向 |
| $M_T$ | 真实导板表面网格 |

上游为第 1 阶段导管、第 2 阶段牙位和第 3 阶段切口计划。

## 1. 导管外壁母线

C 口反方向投影到导管轴法平面：

$$
\mathbf r_i=
\operatorname{unit}\left[-\mathbf d_i^C+
(\mathbf d_i^C\cdot\mathbf a_i)\mathbf a_i\right].
$$

对所属导管对中点 $\mathbf m_i$，必须满足

$$
(\mathbf C_i-\mathbf m_i)\cdot\mathbf r_i>0,
$$

即该母线远离两导管中点。不满足时直接报错，不自动翻转 C 口。

## 2. 高、低 Q 点

导管轴以 C 口高端为原点，$+\mathbf a_i$ 指向闭合低端。高、低接触
位置为

$$
t_i^{U}=t_i^+-c_U-r_c,\qquad
t_i^{L}=t_i^-+c_L+r_c,
$$

其中 $c_U$ 由 `sleeve_stop_clearance_mm` 配置，$c_L$ 由
`anchor_selection.lower_edge_clearance_mm` 配置。
令闭合低端为
$\mathbf E_i=\mathbf C_i+t_i^+\mathbf a_i$，则任一轴向位置 $t$ 的截面中心与外壁
$Q$ 点为

$$
\mathbf O_i(t)=\mathbf E_i-(t-t_i^-)\mathbf a_i,\qquad
\mathbf Q_i(t)=\mathbf O_i(t)+r_s\mathbf r_i.
$$

局部壁厚为

$$
w_i=\|\mathbf Q_i-\mathbf O_i\|-r_b=r_s-r_b.
$$

必须满足 $t_i^U>t_i^L$，且两点与轴向端部的余量不小于配置值。

## 3. 从 Q 到梁中心线 P

若期望梁与导管重叠深度为 $o_i$，则

$$
\mathbf P_i=\mathbf Q_i+(r_c-o_i)\mathbf r_i.
$$

高端为避免挡住标准导孔，使用

$$
o_i^U=\min(2r_c,w_i-c_b),
$$

其中 $c_b$ 是 `anchor_selection.upper_cutter_clearance_mm`。
低端允许全直径预埋：

$$
o_i^L=2r_c.
$$

低梁因此可以进入导孔区，最终在导管、导板和梁架融合后再次复切导孔。

## 4. 牙位射线锚点 A

每个锚点在 YAML 中独立给出牙位站位、U/背 U 侧和射线角。
代码按拓扑使用两种不同标架，不把它们混成一条规则。

### 4.1 单种植位：公共导管标架

将两根导管轴同向对齐后取平均，并使其指向导板外侧 $\mathbf n_T$：

$$
\widetilde{\mathbf a}_2=
\begin{cases}
\mathbf a_2,&\mathbf a_1\cdot\mathbf a_2\ge0,\\
-\mathbf a_2,&\mathbf a_1\cdot\mathbf a_2<0,
\end{cases}
\qquad
\mathbf a_+=\operatorname{orient}_{\mathbf n_T}
\left(\operatorname{unit}(\mathbf a_1+\widetilde{\mathbf a}_2)\right).
$$

$\operatorname{orient}_{\mathbf n_T}$ 只在点积为负时翻转符号，结果还必须满足
$\mathbf a_+\cdot\mathbf n_T\ge0.25$。

令两导管几何轴向中点为 $\mathbf m_1,\mathbf m_2$，公共横向为

$$
\mathbf l=\operatorname{unit}\left[
(\mathbf m_2-\mathbf m_1)-
((\mathbf m_2-\mathbf m_1)\cdot\mathbf a_+)\mathbf a_+
\right].
$$

必须有 $|\mathbf l\cdot\mathbf o_j|\ge0.10$。再定义

$$
\mathbf l_B=
\begin{cases}
\mathbf l,&\mathbf l\cdot\mathbf o_j>0,\\
-\mathbf l,&\mathbf l\cdot\mathbf o_j<0,
\end{cases}
\qquad \mathbf l_U=-\mathbf l_B.
$$

单牙站位使用牙冠点 $\mathbf H_j$，双牙站位使用两牙冠点均值 $\mathbf H$。
射线原点和两侧方向为

$$
\mathbf T=\mathbf H-2\mathbf a_+,
\qquad
\mathbf d_s(\theta)=
\cos\theta\,\mathbf a_++\sin\theta\,\mathbf l_s,
\quad s\in\{U,B\}.
$$

这一标架对应当前单种植位的 `tooth_section_trajectory`
和 `terminal_distal_common_node`。

### 4.2 多种植位连续路径：牙位局部标架

双牙站位使用两牙中心连线，单牙站位使用最近且有导板覆盖的邻牙连线。
记该参考切向为 $\mathbf g$，则

$$
\mathbf n_\Pi=\operatorname{unit}\left[
\mathbf g-(\mathbf g\cdot\mathbf e_{occ})\mathbf e_{occ}
\right],
\qquad
\mathbf l=\operatorname{unit}(\mathbf n_\Pi\times\mathbf e_{occ}).
$$

仍按第 4.1 节中 $\mathbf l\cdot\mathbf o_j$ 的符号得到
$\mathbf l_U,\mathbf l_B$，但此时

$$
\mathbf T=\mathbf H-2\mathbf e_{occ},
\qquad
\mathbf d_s(\theta)=
\cos\theta\,\mathbf e_{occ}+\sin\theta\,\mathbf l_s.
$$

这一标架只对应 `adjacent_two_implant_continuous_paths`
和 `adjacent_two_implant_terminal_distal_node_paths`。

### 4.3 外壁出口

两种标架都使用

$$
\mathbf r(\lambda)=\mathbf T+\lambda\mathbf d_s(\theta),\qquad\lambda\ge0.
$$

求射线与 $M_T$ 的全部交点 $\{(\lambda_k,\mathbf A_k,\mathbf n_k)\}$，合并
$0.02$ mm 内的重复命中，再取第一个外壁出口：

$$
k^*=\min\left\{k:\lambda_k>10^{-5}\ \mathrm{mm},
\mathbf n_k\cdot\mathbf d_s(\theta)\ge0.05\right\},\qquad
\mathbf A=\mathbf A_{k^*}.
$$

这一法向条件跳过内壁入口，选取局部导板真实外表面。

## 5. 最近点备用模式

对未使用牙位射线的 `nearest` 配置，先保留满足全部切口净距的导板样本：

$$
\mathcal S'=\left\{\mathbf x\in\mathcal S:
\min_{c\in\mathcal C}d(\mathbf x,c)\ge c_T
\right\}.
$$

默认 $c_T$ 等于连接梁半径加融合体素尺寸，也可通过
`anchor_selection.clearance_mm` 显式覆盖。解析导孔/操作窗按
有符号距离筛选；轴扫观察窗则读取实际 PLY cutter，同时要求候选点位于切除体外且
到其表面的距离不小于 $c_T$。对导管上下 $P$ 中点 $\mathbf m_P$，左右方向为

$$
\mathbf l_i=\operatorname{unit}(\mathbf a_i\times\mathbf r_i).
$$

按 $(\mathbf x-\mathbf m_P)\cdot\mathbf l_i$ 的符号分左右候选，要求点对跨度

$$
\|\mathbf A^- -\mathbf A^+\|\ge
\max(r_s,2k_s r_c).
$$

其中 $k_s$ 是 `anchor_selection.minimum_span_connector_diameters`；
$2r_c$ 为连接梁直径。

可行点对按

$$
\left(
\|\mathbf A^- -\mathbf m_P\|+\|\mathbf A^+ -\mathbf m_P\|,
\|\mathbf A^- -\mathbf A^+\|,
p^-,p^+
\right)
$$

的字典序取最小，$p^-,p^+$ 为多边形索引。无左右可行点对时报错，
不用同侧两点替代。

## 6. 导管与两侧锚点分配

每个端部必须恰有一个 U 侧和一个背 U 侧锚点。对导管 $i$ 的
高低锚点中点

$$
\mathbf M_i=\frac{\mathbf P_i^-+\mathbf P_i^+}{2},
$$

单种植位在每个端部独立比较

$$
D_{direct}=\|\mathbf A_U-\mathbf M_1\|+
\|\mathbf A_B-\mathbf M_2\|,
$$

$$
D_{reverse}=\|\mathbf A_B-\mathbf M_1\|+
\|\mathbf A_U-\mathbf M_2\|.
$$

取较小者，相等时保留 direct。

多种植位对每个侧别 $s$ 先取路径首尾锚点中点

$$
\overline{\mathbf A}_s=
\frac{\mathbf A_{s,start}+\mathbf A_{s,end}}{2},
$$

再用**两根导管中心** $\mathbf C_1,\mathbf C_2$（不是高低 P 中点）比较

$$
D_{direct}=\|\mathbf C_1-\overline{\mathbf A}_U\|+
\|\mathbf C_2-\overline{\mathbf A}_B\|,
$$

$$
D_{reverse}=\|\mathbf C_2-\overline{\mathbf A}_U\|+
\|\mathbf C_1-\overline{\mathbf A}_B\|.
$$

末端远中公共节点模式只有一个近中端部，也同样用导管中心到该端部 U/背 U 锚点的
direct/reverse 距离分配。最后沿路径首尾中线方向排序各种植位，
形成第 6 阶段的同侧有序路径。

## 质量检查与输出

- 导管数必须为偶数，并按种植位每两根成对；
- $t_i^U>t_i^L$ 且满足轴向余量；
- $w_i>c_b$，否则高端梁会侵入导孔；
- 每条配置射线必须至少有一个有效外壁出口，代码取沿射线的第一个；
- 每个端部必须同时有 U 侧和背 U 侧锚点；
- 所有导板锚点必须来自真实导板表面或明确的特殊结构节点。

末端远中公共节点是例外端点：每根导管只有一个真实导板表面 A 点，另一端是
`DISTAL_COMMON_NODE`；因此“每根导管两个 A 点”只适用于普通双端路径。

`stage-04-anchor-selection.json` 保存 $Q/P/A$、射线和分配关系；
`stage-04-anchor-selection.png` 在真实导管与导板上显示锚点及其射线。

![导管与导板锚点](../images/stage-4-anchor-selection.png)

*tooth-11 完整运行的第 4 阶段结果。红色点为导管 Q/P 锚点，黄色点为导板外壁锚点，黄色细线显示牙位射线和导板交点的来源。*

## 代码对应

| 文档算法 | 当前代码入口 |
| --- | --- |
| 导管 Q/P 与预埋规则 | `sleeve_anchors.select_sleeve_anchors`、`_contact_position` |
| 公共导管标架射线 | `tooth_section_anchors.select_independent_guide_anchors` |
| 多种植位局部标架射线 | `tooth_section_anchors.select_local_independent_guide_anchors` |
| 最近点备用模式 | `template_anchors._remaining_template_samples`、`_select_template_pair` |
| 单/多种植位分配和特殊节点 | `template_anchors.select_template_points` |
