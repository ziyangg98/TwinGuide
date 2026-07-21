"""牙位识别接口。"""

from twin_guide.types import GenerationContext


def identify_tooth_positions(context: GenerationContext) -> None:
    """识别牙位及其几何对应关系。

    参数:
        context: 生成过程的几何结果。

    返回:
        无。

    异常:
        NotImplementedError: 牙位识别尚未实现。

    当前仅保留阶段接口。
    """

    del context
    raise NotImplementedError("第 2 步牙位识别尚未实现")
