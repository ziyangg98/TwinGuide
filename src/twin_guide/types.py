"""生成过程共享的数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from twin_guide.models import CaseAnalysis, GuideSleeve, TemplateFrame

if TYPE_CHECKING:
    from twin_guide.config import CaseConfig
    from twin_guide.models import CutoutPlan
    from twin_guide.point_linking import PointLinkingPlan
    from twin_guide.template_link_points import TemplateLinkPointPlan


class StageMaturity(StrEnum):
    """阶段声明的开发成熟度。"""

    STABLE = "stable"
    EXPERIMENTAL = "experimental"
    PENDING = "pending"
    TODO = "todo"


class StageRunStatus(StrEnum):
    """一次生成流程运行中的阶段实际状态。"""

    COMPLETED = "completed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class StageDefinition:
    """阶段的静态元数据：编号、成熟度、依赖和输出键。"""

    number: int
    key: str
    title_zh: str
    maturity: StageMaturity
    implementation_version: str | None
    requires: tuple[str, ...]
    provides: str | None


@dataclass(frozen=True, slots=True)
class StageResult:
    """一个阶段的运行记录。

    完成阶段必须提供 ``output``；跳过阶段必须提供 ``reason``。
    """

    definition: StageDefinition
    status: StageRunStatus
    output: object | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        """校验完成阶段必须有输出、跳过阶段必须有原因。"""

        if self.status is StageRunStatus.COMPLETED and self.output is None:
            raise ValueError("已完成阶段必须提供输出")
        if self.status is StageRunStatus.SKIPPED and not self.reason:
            raise ValueError("已跳过阶段必须提供原因")


@dataclass(frozen=True, slots=True)
class SleeveGenerationResult:
    """第 1 步输出，与牙位、切窗和后续连建无关。"""

    sleeves: tuple[GuideSleeve, GuideSleeve]
    template_frame: TemplateFrame | None = None


@dataclass(slots=True)
class GenerationContext:
    """阶段间显式传递的上下文。

    字段为 ``None`` 表示该阶段没有执行。
    """

    config: CaseConfig
    case: CaseAnalysis | None = None
    sleeve_generation: SleeveGenerationResult | None = None
    tooth_identification: object | None = None
    window_cutouts: CutoutPlan | None = None
    template_link_points: TemplateLinkPointPlan | None = None
    press_beam_points: object | None = None
    point_linking: PointLinkingPlan | None = None
    clearance_adjustment: object | None = None


@dataclass(frozen=True, slots=True)
class GenerationProcessResult:
    """生成过程的上下文和阶段状态记录。"""

    context: GenerationContext
    stages: tuple[StageResult, ...] = field(default_factory=tuple)

    def stage(self, number: int) -> StageResult:
        """按从 1 开始的阶段编号返回运行结果。"""

        return next(result for result in self.stages if result.definition.number == number)

    @property
    def completed_outputs(self) -> dict[str, object]:
        """返回状态为 ``completed`` 的命名输出。"""

        return {
            result.definition.provides: result.output
            for result in self.stages
            if result.status is StageRunStatus.COMPLETED and result.definition.provides is not None
        }
