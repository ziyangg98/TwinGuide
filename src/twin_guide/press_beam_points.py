"""按压梁柱选点接口。"""

from twin_guide.types import GenerationContext


def select_press_beam_points(context: GenerationContext) -> None:
    """选择按压梁柱的结构点。

    参数:
        context: 生成过程的几何结果。

    返回:
        无。

    异常:
        NotImplementedError: 按压梁柱选点尚未实现。

    当前仅保留阶段接口。
    """

    del context
    raise NotImplementedError("第 5 步按压梁柱选点尚未实现")
