# 2. 牙位识别与导板映射

**实现状态：实验。** 第二阶段已接入生成流程。它不创建任何梁或切除体，
只把牙列 STL 变成带 FDI 编号的三维牙位，并测出这些牙位在牙科导板上的对应位置。

## 输入

| 输入 | 来自 `case.yaml` 的内容 | 实际用途 |
| --- | --- | --- |
| 患者牙列 | `objects.dental.path` | 识别真实牙冠轮廓和牙冠顶部 |
| 牙科导板 | `objects.guide.path` | 测量每个牙位是否被导板覆盖以及导板顶部位置 |
| 上下颌与牙位 | `anatomy.jaw`、`fdi_order`、`present_teeth`、`missing_teeth`、`excluded_teeth` | 规定允许出现的 FDI 和排列顺序 |
| 病例坐标轴 | `anatomy.orientation` | 把任意 STL 坐标转换为患者左右、前后和牙合方向 |
| 观察窗端点 | `design.observation_windows` | 指定后续观察窗从哪颗现存牙开始、到哪颗现存牙结束 |

`present_teeth` 是需要从网格中找到真实轮廓的牙；`missing_teeth` 只在牙弓上留下
间隙；`excluded_teeth` 不参与编号。程序不会根据几何形状自行创造配置之外的牙位。

## 坐标、牙弓与投影公式

令病例原点为 $\mathbf o$，正交单位基为
$(\mathbf e_{lr},\mathbf e_{ap},\mathbf e_{occ})$。任意三维点 $\mathbf x$ 的病人坐标为

$$
u=(\mathbf x-\mathbf o)\cdot\mathbf e_{lr},\qquad
v=(\mathbf x-\mathbf o)\cdot\mathbf e_{ap},\qquad
h=(\mathbf x-\mathbf o)\cdot\mathbf e_{occ}.
$$

未提供确认轴时，初始牙合轴取牙列协方差矩阵的最小特征值方向：

$$
\mathbf C=\frac1{N-1}\sum_i(\mathbf x_i-\mathbf o)(\mathbf x_i-\mathbf o)^\mathsf T,
\qquad
\mathbf e_{occ}=\operatorname*{arg\,min}_{\|\mathbf e\|=1}\mathbf e^\mathsf T\mathbf C\mathbf e.
$$

它的符号使导板中心位于正牙合侧。完整三轴已在 YAML 给定时，上式
只作为背景定义，代码直接使用确认轴。

牙冠点集的高度与法向资格为

$$
\mathcal V_q=
\left\{\mathbf x_i:
h_i\ge Q_q(h),\;
\mathbf n_i\cdot\mathbf e_{occ}\ge\tau_n
\right\},
$$

其中默认 $q=0.55$、$\tau_n=0.05$。投影像素 $(p,q)$ 保留同一 LR/AP
格点上的最高表面：

$$
H_{pq}=\max\left\{h_i:(u_i,v_i)\in\text{pixel}(p,q),\;\mathbf x_i\in\mathcal V_q\right\}.
$$

令有向牙弓曲线为 $\boldsymbol\gamma(s)$，$s$ 为毫米弧长。每个物理牙冠
轮廓 $\Omega_j$ 的面积中心为

$$
(\bar u_j,\bar v_j)=
\frac{1}{|\Omega_j|}\iint_{\Omega_j}(u,v)\,\mathrm dA.
$$

其牙弓位置是到该中心的最近曲线参数

$$
s_j=\operatorname*{arg\,min}_{s}
\left\|\boldsymbol\gamma(s)-(\bar u_j,\bar v_j)\right\|^2.
$$

局部切向与牙弓平面内外向为

$$
\mathbf t_j=\frac{\boldsymbol\gamma'(s_j)}{\|\boldsymbol\gamma'(s_j)\|},
\qquad
\mathbf o_j=\operatorname{unit}(\mathbf e_{occ}\times\mathbf t_j),
$$

并用病例已确认的牙弓外侧语义统一 $\mathbf o_j$ 的符号。最终三维牙冠点为

$$
\mathbf c_j=\mathbf o+\bar u_j\mathbf e_{lr}+\bar v_j\mathbf e_{ap}
+H(\bar u_j,\bar v_j)\mathbf e_{occ}.
$$

## 实际计算顺序

### 1. 建立患者牙弓坐标

程序以牙列顶点均值为原点，建立三个单位向量：

- `e_lr`：患者右侧指向左侧；
- `e_ap`：前牙指向后牙；
- `e_occ`：牙合外向。

病例给出完整三轴时直接采用；否则使用牙列 PCA 得到牙弓平面，并按牙弓几何
确定 `e_lr` 的符号。生产病例应显式确认三轴，避免左右语义存在歧义。

上颌或下颌只能校验 FDI 象限和牙合语义，不能单独确定任意 STL 文件中的左右轴。

### 2. 从三维牙列提取牙冠表面

每个顶点的牙合高度是前述
$h_i=(\mathbf x_i-\mathbf o)\cdot\mathbf e_{occ}$，同时按
$\mathbf n_i\cdot\mathbf e_{occ}$ 排除背向牙合面。

当前算法先取高度分位数 `0.55`，并要求顶点法向与 `e_occ` 的点积不低于 `0.05`，
以排除牙龈侧面和朝下表面。选中的三角面以 `0.12 mm` 网格投影到 LR/AP 牙弓平面，
每个像素保留最高表面的高度和法向，而不是只画稀疏顶点。

基础牙弓拟合使用顶点筛选时，如果“高度 + 法向”共同筛出的顶点少于 1000 个，
`select_crown_points` 会保留同一高度阈值、仅取消法向门槛；该分支是短牙冠或法向质量不足时
建立坐标系的保底，不会直接制造牙冠实例。

若投影中可分辨的物理牙冠数量与 `present_teeth` 数量不一致，高度分位数依次尝试
`0.55`、`0.50`、`0.45`、`0.40`，但不会通过增加或删除 FDI 来凑数。

### 3. 把连续投影分成一颗颗牙

程序在牙弓中心线两侧 `11.5 mm` 范围内寻找牙冠核心，并按牙弓弧长排序。
默认 `arch_progress` 策略只合并具有局部间距证据的相邻核心；若仍存在彼此分离的
多余核心，可保留严格有序、与现存牙先验数目相同的一组，其余投影不编号。若最终物理核心
少于或多于 `present_teeth`，后续质量检查失败，不能用 FDI 槽位裁剪来补出缺失实例。

相邻牙冠的主分隔算法不是中点直线族，而是连接实测外轮廓凹点的最短有效弦。
对相邻拓扑核心 $\mathbf c_i,\mathbf c_{i+1}$，令

$$
\mathbf e=\frac{\mathbf c_{i+1}-\mathbf c_i}
{\|\mathbf c_{i+1}-\mathbf c_i\|},\qquad
\mathbf q=(-e_2,e_1),\qquad
\mathbf m=\frac{\mathbf c_i+\mathbf c_{i+1}}2.
$$

代码在共同外轮廓的两侧分别选凹点，连接成候选弦。候选必须同时满足：端点来自
实测轮廓；弦长 $1.8\le L\le12.0$ mm；弦方向不能近似平行于两核心连线；
两核心到弦的有符号距离异号且绝对值均不小于 `0.40 mm`；弦内部落在牙冠投影内的
采样比例不低于 `0.86`；两侧局部区域面积均达到下限；并且不与已接受分隔弦相交。

每条有效弦的证据分数为

$$
E=0.42E_{concavity}+0.24E_{endpoint}+0.15E_{line}
+0.10E_{height}+0.09E_{normal},
$$

分别衡量端点凹度、端点边缘、弦上边缘、弦两侧相对高度谷和法向跳变。
代码先取距最短弦不超过 `0.30 mm` 的候选，再从中取 $E$ 最大者，而不是最小化
旧版的“长度 + 中心偏移 + 角度偏移”目标。

搜索顺序固定为：尖锐尺度凹点、较宽尺度凹点、接触区局部颈部。当前质量门只自动
批准 `shortest_valid_concavity_pair`。宽尺度结果会保留诊断，但当前没有可配置批准入口，
因此会使 `all_contacts_use_approved_anatomical_separators` 失败；局部颈部仍要求两个端点
都是真实外轮廓点，并且必须由病例对该牙间位置显式批准。三类均失败才生成
`legacy_midline_fallback` 诊断弦并标为 `uncertain`；该结果会使
`no_uncertain_contact_chords` 失败，不能进入下游。

相邻 FDI 之间配置了缺失牙时，该位置使用明确的 `gap` 分隔。所有有限弦必须
互不交叉，每个牙冠核心必须落在自己的半平面分区 $\Omega_j$ 内，且

$$
\frac{\left|\bigcup_j\Omega_j\right|}{|\Omega_{crown}|}\ge0.99.
$$

### 4. 按病例顺序赋予 FDI

程序沿有方向的牙弓依次排列牙冠轮廓，并按 `fdi_order` 中的现存牙顺序编号。
例如 tooth-11 病例的顺序为：

```text
17 16 15 14 13 12 [缺失 11] 21 22 23 24 25 26 27
```

因此该病例应得到 13 个真实牙冠轮廓、12 条相邻分隔线，其中跨越缺失 11 的一条
分隔线类型为 `gap`。缺失 11 有语义位置，但没有伪造的三维牙冠中心。

### 5. 把二维轮廓还原为三维牙位

每颗牙的 LR/AP 位置取最终轮廓的面积中心，再从增强投影保存的最高表面高度将该点
抬回牙冠表面，得到 `dental_crown_point_global_mm`。牙弓曲线在该点的导数给出
`local_tangent_global`，牙弓平面内与切向垂直且朝外的方向给出
`local_outward_global`。

这里使用的是完整物理轮廓的面积中心，不是初始峰值、椭圆中心或预设牙宽中心。

### 6. 测量牙位对应的导板表面

程序在每个牙位建立由局部切向、局部外向和牙合轴组成的截面，检查导板的真实闭合
截面。找到朝牙弓外侧的导板表面时记录 `guide_top_global_mm`；该牙位超出导板范围时
记录 `outside_guide_coverage`，不会把最近顶点冒充导板顶部。

对截面闭合环上任意点 $\mathbf x$，定义局部坐标

$$
u=(\mathbf x-\mathbf b)\cdot\mathbf o_j,\qquad
h=(\mathbf x-\mathbf b)\cdot\mathbf e_{occ}.
$$

代码只保留满足

$$
n_u(\mathbf x)\ge0.10,\qquad u(\mathbf x)\ge0.25\ \mathrm{mm}
$$

的连通外侧弧，并以最外侧合格点所在连通分量为真实外壁。从该分量的
最高点沿较短环路走向全局脊顶，路径最长取 3 mm，该路径 $\mathcal R_j$
上的导板顶点是

$$
\mathbf g_j=
\operatorname*{arg\,max}_{\mathbf x\in\mathcal R_j}
\mathbf x\cdot\mathbf e_{occ}.
$$

超出覆盖范围可以是正常结果，例如导板没有延伸到末端磨牙；但后续锚点明确要求的
牙位必须具有真实导板覆盖。

### 7. 映射观察窗端点

每个观察窗的 `start_fdi` 和 `end_fdi` 必须是已经测量的现存牙。第二阶段根据两颗
端点牙的牙弓位置和局部方向生成观察窗语义轴或顶部脊线采样；第三阶段再沿这些结果
构造实际观察窗切除体。

第二阶段不进行观察窗布尔运算，也不生成锚点、按压梁或连接梁。

令两端牙冠高度为 $h_0,h_1$，配置下移量为 $d$，共同观察轴高度为

$$
h_* = \max(h_0,h_1)-d.
$$

将两端牙冠点沿 $\mathbf e_{occ}$ 移到 $h_*$ 得
$\mathbf g_0,\mathbf g_1$，则

$$
\mathbf e_g=\frac{\mathbf g_1-\mathbf g_0}{\|\mathbf g_1-\mathbf g_0\|},
\qquad
\mathbf e_0=
\operatorname{unit}\left[
\mathbf e_{occ}-(\mathbf e_{occ}\cdot\mathbf e_g)\mathbf e_g
\right].
$$

正 $90^\circ$ 方向 $\mathbf e_{90}$ 是两端局部外向的平均在
$\operatorname{span}(\mathbf e_g,\mathbf e_0)$ 正交补空间上的归一化投影，并使其朝牙弓外侧。
第 3 阶段只消费 $(\mathbf g_0,\mathbf g_1,\mathbf e_0,\mathbf e_{90})$。

## 输出给后续阶段的数据

`ToothIdentificationResult` 是阶段接口：

| 字段 | 内容 | 主要使用者 |
| --- | --- | --- |
| `fdi_order`、`present_teeth`、`missing_teeth`、`excluded_teeth` | 经过校验的病例牙位语义 | 第 3–6 阶段 |
| `positions[].crown_point` | 每颗现存牙的真实三维牙冠点 | 锚点、按压梁 |
| `positions[].arch_s_mm` | 该牙位沿有方向牙弓的距离 | 排序和牙间区间 |
| `positions[].local_tangent` | 该牙位的近远中方向 | 锚点和特殊末端结构 |
| `positions[].local_outward` | 从牙弓内部指向外侧 | 导板表面搜索和梁方向 |
| `positions[].guide_top` | 真实导板顶部；无覆盖时为 `None` | 导板端锚点 |
| `windows[].crest_points` | 观察窗映射的诊断点；轴扫模式的实体输入是 `axis_sweep` 中的轴端点和方向 | 第 3 阶段观察窗 |

后续阶段只消费这些类型化结果，不重新识别牙齿，也不重新选择坐标方向。

磁盘输出为一对同名产物：

```text
output/<case_id>/
├── stage-02-tooth-mapping.json   # 完整第二阶段结果
├── stage-02-tooth-mapping.png    # 阶段结果图
└── .cache/
    └── stage-02-tooth-mapping/   # 内部计算缓存，不属于公开接口
```

`stage-02-tooth-mapping.json` 同时包含病例语义、坐标系、牙位、观察窗、质量检查和输入指纹。
程序复用缓存时先检查该文件中的输入指纹；后续模块需要文件路径时也只引用该文件。
`.cache` 中的基础映射、投影数组、接触弦报告和内部清单可以重新计算，不能作为
第三至第七阶段的输入。

## tooth-11 实际结果

下表来自一次完整 `generate --validate` 运行中的第二阶段报告：

| 项目 | 实际结果 |
| --- | --- |
| 坐标方向 | PCA 初始平面，并由缺失牙到手术位的一致性确定左右 |
| 配置牙位 | 14 个语义槽位：13 颗现存牙、缺失 11 |
| 牙冠投影 | 94,752 个三角面，53,653 个有效像素，分辨率 0.12 mm |
| 高度选择 | 首次使用 0.55 分位数即得到 13 个物理牙冠核心 |
| 牙冠分区 | 13 个轮廓、12 条分隔线，投影覆盖率 100% |
| 导板覆盖 | 15、14、13、12、21、22、23、24、25 有覆盖；17、16、26、27 超出覆盖范围 |
| 观察窗 | 15→14 和 24→25 两个轴扫窗；每条语义轴 14 个截面 |

多种植位病例仍然只执行一次完整牙弓识别。多个缺失种植位表现为同一 FDI 序列中的
多个 `gap`，不会按种植位重复分割牙列。

## 参数与实现边界

| 参数 | 当前值 | 作用 |
| --- | --- | --- |
| 牙冠高度分位数 | 0.55，最低尝试到 0.40 | 去除低位牙龈和侧壁 |
| 最小牙合法向点积 | 0.05 | 去除明显背向牙合面的表面 |
| 增强投影分辨率 | 0.12 mm | 计算连续高度、法向和边缘图 |
| 轮廓计算分辨率 | 0.18 mm | 接触弦和牙冠区域分割 |
| 牙弓走廊半宽 | 11.5 mm | 排除远离牙弓的投影干扰 |
| 最小投影分区覆盖率 | 0.99 | 防止大量牙冠区域未分配 |

## 代码对应

| 文档算法 | 当前代码入口 |
| --- | --- |
| 坐标系、牙弓与导板覆盖 | `tooth_mapping.pipeline._core.estimate_frame_and_arch`、`guide_physical_coverage_top` |
| 高度阈值试探与增强投影 | `render_enhanced_crown_projection._quantile_trials`、`run` |
| 物理牙冠核心分组 | `arch_progress_core_grouping.select_crown_core_candidates` |
| 凹点弦搜索与回退 | `contact_chords.find_shortest_concavity_chords` |
| 分区与 fail-closed 质量门 | `extract_contact_chord_contours.run` |
| 观察窗语义轴 | `contact_guide_mapping.map_axis_sweep` |

本阶段生成 `stage-02-tooth-mapping.png`，并在 `.cache` 中保存投影数组和
内部报告。`write_diagnostics` 控制详细诊断 PNG 和 GLB。同一次运行重复使用的
牙列、导板和手术参考网格按文件状态复用。

## 失败条件

以下任一情况都会停止第二阶段：

- FDI 集合交叉、顺序不完整或与上下颌不符；
- 物理牙冠核心数、最终轮廓数与 `present_teeth` 数量不一致；
- 接触弦不确定、相交，或牙冠投影覆盖率低于 99%；
- 牙位中心没有沿牙弓严格递增；
- 自动推断的左右方向仍存在歧义；
- 后续锚点需要的牙位没有真实导板覆盖；
- 观察窗端点不是已测量的现存牙。

## 结果图如何阅读

![牙位识别与导板映射](../images/stage-2-tooth-mapping.png)

*tooth-11 完整运行的牙冠投影与 FDI 映射结果。重点检查牙冠边界、接触分隔、FDI 次序和观察窗两端是否与实际牙位一致。*

上图是实际牙冠投影：浅灰色为牙冠表面，彩色轮廓为接触弦分割后的物理牙冠，
深色短线为真实接触分隔，圆点为轮廓面积中心，深灰曲线为有向牙弓。红色菱形表示
`case.yaml` 中的缺牙槽位，半透明色带表示观察窗对应的牙弓段。

下图与上图共用同一有向牙弓坐标 $s$：灰色曲线为牙冠支持度，底部色带为实测牙冠区间，
红色斜纹为缺牙槽位，顶部粗线为观察窗范围。因此牙冠轮廓、FDI、缺牙语义和窗口范围可以直接上下对齐审核。
