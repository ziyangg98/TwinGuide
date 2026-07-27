"""TwinGuide 可切换的阶段算法策略。"""

from twin_guide.strategies.connectors import build_point_linking_plan
from twin_guide.strategies.observation_windows import plan_observation_windows

__all__ = ["build_point_linking_plan", "plan_observation_windows"]
