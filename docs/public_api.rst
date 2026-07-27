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

第 1 步：导管识别与实体模式选择
----------------------------------------

.. autofunction:: twin_guide.recognize_and_build_sleeves

.. autoclass:: twin_guide.SleeveGenerationInputs
   :members:

.. autoclass:: twin_guide.SleeveGenerationResult
   :members:

每个 ``GuideSleeve.parameters`` 使用 ``SleeveEstimate`` 保存轴线、世界坐标中的
``c_opening_direction`` 单位向量和八个标量尺寸参数。候选组件必须具有轴向孔道；
``generated`` 模式按参数重建标准导管，``input`` 模式直接保存所选输入组件。

第 2 步：牙位识别与导板映射
----------------------------------------

本接口读取病例 YAML，现场执行 FDI 牙位识别、方向校验和导板映射；未配置
牙位工作流时由生成控制层将该阶段标记为 ``skipped``。

.. autofunction:: twin_guide.identify_tooth_positions

第 3 步：导孔与窗口规划
----------------------------------------

现有接口读取病例分析、当前模式导管和牙位映射结果，生成导孔、操作窗及
按 FDI 区间构造的轴扫掠观察窗。

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

第 5 步：按压梁柱锚点选择
----------------------------------------

本接口按病例配置选择混合导管锚点、三牙位锚点或末端 U 型延伸锚点，
并生成三臂 Y 梁的汇合点计划；未配置时由生成控制层标记为 ``skipped``。

.. autofunction:: twin_guide.select_press_beam_points

第 6 步：光滑连接管生成
----------------------------------------

当前接口对每个导套生成上下两根连续导套—牙科导板梁，总数为四根。
配置按压梁时，当前接口同时生成三根 Y 型按压梁及汇合球计划。

.. autofunction:: twin_guide.link_selected_points

.. autoclass:: twin_guide.PointLinkingConfig
   :members:

.. autoclass:: twin_guide.PointLinkingPlan
   :members:

第 7 步：手机避让空间调整
----------------------------------------

配置手机 STL 与止挡报告后，本接口生成或复用当前深度左右摆动封闭包络。

.. autofunction:: twin_guide.adjust_clearance

.. autoclass:: twin_guide.HandpieceAvoidancePlan
   :members:

Blender 建模
----------------------------------------

.. autofunction:: twin_guide.blender.guide_modeling.create_point_link_meshes

.. autofunction:: twin_guide.blender.guide_modeling.build_guide_from_links

异常类型
--------

.. automodule:: twin_guide.errors
