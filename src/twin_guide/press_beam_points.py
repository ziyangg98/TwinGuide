"""按压梁柱选点接口。"""

from twin_guide.types import GenerationContext


def select_press_beam_points(context: GenerationContext) -> None:
    """选择按压梁柱的结构点。

    参数:
        context: 生成过程的几何结果。

    返回:
        无。函数实现后将改为按压梁柱选点结果类型。

    异常:
        NotImplementedError: 按压梁柱选点尚未实现。

    算法说明:
        目标实现使用牙位区域建立支撑候选，剔除与导孔、窗口和联建结构
        净距不足的候选，再选择按压梁柱的两端点。
    """

    del context
    raise NotImplementedError("第 5 步按压梁柱选点尚未实现")
