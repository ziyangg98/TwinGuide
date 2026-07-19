生成顺序
========

阶段执行
--------

程序按固定顺序执行可用阶段。未完成阶段记录为 ``skipped``，
对应输出保持为 ``None``。

.. automodule:: twin_guide.types

.. automodule:: twin_guide.generation_process

第 1 步：导套识别与生成
----------------------------------------

输出导套参数、闭合重建结果和质量诊断。分量分离、参数拟合、
切片和完整性检查均属于本阶段。

.. automodule:: twin_guide.sleeve_generation

.. automodule:: twin_guide.case_analysis

.. automodule:: twin_guide.sleeve_estimation.types

.. automodule:: twin_guide.sleeve_estimation.fitting

.. automodule:: twin_guide.sleeve_estimation.slicing

.. automodule:: twin_guide.sleeve_estimation.sleeve

.. automodule:: twin_guide.sleeve_estimation.mesh_integrity

.. automodule:: twin_guide.sleeve_estimation.validation

第 2 步：牙位识别
----------------------------------------

该步尚未实现，运行时记录为 ``skipped``。

.. automodule:: twin_guide.tooth_identification

第 3 步：操作窗和观察窗切口
----------------------------------------

根据牙科导板分析和第 1 步结果生成通道、操作窗和观察窗计划。

.. automodule:: twin_guide.window_cutouts

第 4 步：导套与牙科导板联建选点
----------------------------------------

输出导套上下锚点、牙科导板左右点和选点诊断，不生成连接中心线。

.. automodule:: twin_guide.sleeve_anchors

.. automodule:: twin_guide.template_anchors

.. automodule:: twin_guide.clearance

.. automodule:: twin_guide.template_link_points

第 5 步：按压梁柱选点
----------------------------------------

该步尚未实现，运行时记录为 ``skipped``。

.. automodule:: twin_guide.press_beam_points

第 6 步：选点连接
----------------------------------------

根据第 4 步输出生成 Bézier 控制点、离散中心线和连接半径。
输出导套与牙科导板之间的连接。

.. automodule:: twin_guide.point_linking

第 7 步：避让空间调整
----------------------------------------

该步尚未实现，运行时记录为 ``skipped``。固定孔复切属于第 6 步。

.. automodule:: twin_guide.clearance_adjustment

生成入口
--------

``generate_guide`` 将第 1、3、4、6 步的结果实体化，然后导出 STL 和过程图。

.. automodule:: twin_guide.config

.. automodule:: twin_guide.models

.. automodule:: twin_guide.guide_generation

.. automodule:: twin_guide.blender.guide_modeling

.. automodule:: twin_guide.guide_validation

.. automodule:: twin_guide.cli
