"""Twinguide 专用异常类型。"""


class TwinGuideError(RuntimeError):
    """Twinguide 错误的基类。"""


class ConfigurationError(TwinGuideError):
    """表示病例配置无效。"""


class MeshIOError(TwinGuideError):
    """表示网格导入或导出失败。"""


class GeometryError(TwinGuideError):
    """表示输入几何无法满足构建约束。"""


class BooleanOperationError(TwinGuideError):
    """表示所有已支持求解器均无法完成布尔运算。"""
