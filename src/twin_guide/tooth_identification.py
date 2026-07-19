"""第 2 步预留模块：牙位识别。"""

from twin_guide.types import GenerationContext


def identify_tooth_positions(context: GenerationContext) -> None:
    """声明第 2 步未来接口。

    参数:
        context: 生成流程上下文，当前仅用于锁定未来签名。

    返回:
        本预留函数不返回有效结果。

    异常:
        NotImplementedError: 始终抛出，防止伪造牙位输出。
    """

    del context
    raise NotImplementedError("第 2 步牙位识别尚未实现")
