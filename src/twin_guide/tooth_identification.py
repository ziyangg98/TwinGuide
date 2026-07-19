"""牙位识别接口。"""

from twin_guide.types import GenerationContext


def identify_tooth_positions(context: GenerationContext) -> None:
    """识别牙位及其几何对应关系。

    参数:
        context: 生成过程的几何结果。

    返回:
        无。函数实现后将改为牙位识别结果类型。

    异常:
        NotImplementedError: 牙位识别尚未实现。

    算法说明:
        目标实现将患者牙列网格转换到病例坐标系，分割单颗牙齿，
        再根据牙弓顺序赋予牙位标签并返回表面区域。
    """

    del context
    raise NotImplementedError("第 2 步牙位识别尚未实现")
