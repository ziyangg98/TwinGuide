"""第 5 步预留模块：按压梁柱选点。"""

from twin_guide.types import GenerationContext


def select_press_beam_points(context: GenerationContext) -> None:
    """声明第 5 步未来接口。

    参数:
        context: 生成流程上下文，当前仅用于锁定未来签名。

    返回:
        本预留函数不返回有效结果。

    异常:
        NotImplementedError: 始终抛出，防止伪造按压梁柱选点。
    """

    del context
    raise NotImplementedError("第 5 步按压梁柱选点尚未实现")
