Python API 参考
===================

公开接口统一从 ``twin_guide`` 导入。几何长度和距离的单位为毫米。

程序入口
--------

.. autofunction:: twin_guide.generate_guide

.. autofunction:: twin_guide.run_generation_process

.. autofunction:: twin_guide.validate_guide

配置与公共数据
----------------------------------------

.. autoclass:: twin_guide.CaseConfig
   :members:

.. autoclass:: twin_guide.SleeveParameters
   :members:

.. autoclass:: twin_guide.GenerationContext
   :members:

.. autoclass:: twin_guide.GenerationProcessResult
   :members:

.. autoclass:: twin_guide.StageResult
   :members:

.. autoclass:: twin_guide.BuildArtifacts
   :members:

.. autoclass:: twin_guide.ValidationResult
   :members:

第 1 步：导套识别与参数化重建
----------------------------------------

.. autofunction:: twin_guide.recognize_and_build_sleeves

.. autoclass:: twin_guide.SleeveGenerationInputs
   :members:

.. autoclass:: twin_guide.SleeveGenerationResult
   :members:

每个 ``GuideSleeve.parameters`` 使用 ``SleeveEstimate`` 保存轴线、世界坐标中的
``c_opening_direction`` 单位向量和八个标量尺寸参数。两个 C 口分别指向对侧导柱，
八个标量尺寸由病例配置提供。

第 2 步：牙位识别（待实现）
----------------------------------------

本接口目前仅保留公开函数，调用时抛出 ``NotImplementedError``。

.. autofunction:: twin_guide.identify_tooth_positions

第 3 步：导孔与窗口规划
----------------------------------------

现有接口读取病例分析和导套重建结果，生成导孔、操作窗和前牙观察缺口。
观察缺口对准前牙牙面，下缘刚好露出牙齿。

.. autofunction:: twin_guide.plan_window_cutouts

.. autoclass:: twin_guide.WindowCutoutPlan
   :members:

第 4 步：导套与牙科导板联建锚点选择
----------------------------------------

.. autofunction:: twin_guide.select_sleeve_anchors

.. autofunction:: twin_guide.select_template_points

.. autofunction:: twin_guide.select_template_link_points

.. autoclass:: twin_guide.TemplatePointSelectionConfig
   :members:

.. autoclass:: twin_guide.TemplateLinkPointContext
   :members:

.. autoclass:: twin_guide.TemplateLinkPointPlan
   :members:

第 5 步：按压梁柱锚点选择（待实现）
----------------------------------------

本接口目前仅保留公开函数，调用时抛出 ``NotImplementedError``。

.. autofunction:: twin_guide.select_press_beam_points

第 6 步：光滑连接管生成
----------------------------------------

当前接口对每个导套生成四条导套—牙科导板连接，总数为八条。
当前接口尚不包含按压梁柱连接。

.. autofunction:: twin_guide.link_selected_points

.. autoclass:: twin_guide.PointLinkingConfig
   :members:

.. autoclass:: twin_guide.PointLinkingPlan
   :members:

第 7 步：避让空间调整（待实现）
----------------------------------------

本接口目前仅保留公开函数，调用时抛出 ``NotImplementedError``。

.. autofunction:: twin_guide.adjust_clearance

Blender 建模
----------------------------------------

.. autofunction:: twin_guide.blender.guide_modeling.create_point_link_meshes

.. autofunction:: twin_guide.blender.guide_modeling.build_guide_from_links

异常类型
--------

.. automodule:: twin_guide.errors
