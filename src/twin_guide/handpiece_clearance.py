"""牙科手机净距说明。

手机当前深度左右摆动包络由 :mod:`twin_guide.clearance_adjustment`
生成，并在最终 Blender 实体化阶段作为整体直接差集 cutter 使用。
``validate_guide()`` 当前不重复计算运动包络。
"""
