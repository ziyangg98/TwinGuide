"""第 7 步预留模块：避让空间调整。"""

from twin_guide.types import GenerationContext


def adjust_clearance(context: GenerationContext) -> None:
    """声明第 7 步未来接口。

    参数:
        context: 生成流程上下文，当前仅用于锁定未来签名。

    返回:
        本预留函数不返回有效结果。

    异常:
        NotImplementedError: 始终抛出，不将净距验证冒充为几何调整。
    """

    del context
    raise NotImplementedError("第 7 步避让空间调整尚未实现")
