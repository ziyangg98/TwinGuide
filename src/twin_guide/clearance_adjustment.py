"""避让空间调整接口。"""

from twin_guide.types import GenerationContext


def adjust_clearance(context: GenerationContext) -> None:
    """调整导板结构的避让空间。

    参数:
        context: 生成过程的几何结果。

    返回:
        无。

    异常:
        NotImplementedError: 避让空间调整尚未实现。

    当前仅保留阶段接口。
    """

    del context
    raise NotImplementedError("第 7 步避让空间调整尚未实现")
