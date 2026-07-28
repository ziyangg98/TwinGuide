Python API 参考
===================

TwinGuide 0.3 的稳定接口统一从 ``twin_guide`` 导入。锚点选择、牙位映射、
网格构造和 Blender 实体化模块属于内部实现，不提供跨版本兼容承诺。

配置
----

.. autoclass:: twin_guide.CaseConfig
   :members: from_yaml

``CaseConfig.from_yaml()`` 读取一份完整病例 YAML。所有输入路径相对于该 YAML
所在目录解析；默认运行输出位于代码仓库的 ``output/<case_id>``。

运行入口
--------

.. autofunction:: twin_guide.run_generation_process

.. autofunction:: twin_guide.generate_guide

.. autofunction:: twin_guide.validate_guide

结果类型
--------

.. autoclass:: twin_guide.GenerationProcessResult
   :members:

.. autoclass:: twin_guide.StageResult
   :members:

.. autoclass:: twin_guide.BuildArtifacts
   :members:

.. autoclass:: twin_guide.ValidationResult
   :members:

``run_generation_process()`` 返回七个阶段的状态和共享上下文；
``generate_guide()`` 返回 STL 与诊断图路径；``validate_guide()`` 返回独立检查结果，
不会修改待检查模型。
