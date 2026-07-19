"""避让空间调整接口。"""

from twin_guide.types import GenerationContext


def adjust_clearance(context: GenerationContext) -> None:
    """调整导板结构的避让空间。

    参数:
        context: 生成过程的几何结果。

    返回:
        无。函数实现后将改为避让空间调整结果类型。

    异常:
        NotImplementedError: 避让空间调整尚未实现。

    算法说明:
        目标实现计算连接结构与患者牙列、牙科手机运动包络和功能保护区的净距，
        并在保持端点与连接半径的条件下调整曲线控制点。
    """

    del context
    raise NotImplementedError("第 7 步避让空间调整尚未实现")
