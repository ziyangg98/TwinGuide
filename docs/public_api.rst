Python 公开接口
=================

业务代码只需从 ``twin_guide`` 包导入类型和函数。

.. code-block:: python

   from twin_guide import (
       PointLinkingConfig,
       TemplateLinkPointContext,
       TemplatePointSelectionConfig,
       link_selected_points,
       run_generation_process,
       select_template_link_points,
   )

程序入口
--------

.. autofunction:: twin_guide.run_generation_process

.. autofunction:: twin_guide.generate_guide

.. autofunction:: twin_guide.validate_guide

导套和切口
----------------------------------------

.. autofunction:: twin_guide.recognize_and_build_sleeves

.. autofunction:: twin_guide.plan_window_cutouts

联建选点和连接
----------------------------------------

.. autofunction:: twin_guide.select_sleeve_anchors

.. autofunction:: twin_guide.select_template_points

.. autofunction:: twin_guide.select_template_link_points

.. autofunction:: twin_guide.link_selected_points

主要数据类型
----------------------------------------

.. autoclass:: twin_guide.GenerationContext
   :members:

.. autoclass:: twin_guide.GenerationProcessResult
   :members:

.. autoclass:: twin_guide.TemplateLinkPointPlan
   :members:

.. autoclass:: twin_guide.PointLinkingPlan
   :members:
