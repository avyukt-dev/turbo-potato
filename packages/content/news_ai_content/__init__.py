"""Stage-21 factual-boundary content generation package."""

from .brief import build_editorial_brief
from .configuration import (
    ContentStyleConfig,
    ContentStyleConfigLoader,
    ContentStyleDefaults,
)
from .contracts import (
    BriefClaim,
    CarouselSlide,
    ContentDraftArtifact,
    ContentFormat,
    ContentGenerationOutput,
    ContentPlatform,
    ContentTarget,
    ContentVariantArtifact,
    EditorialBrief,
    InstagramCarouselContent,
)
from .generation import (
    ContentGenerationContext,
    ContentGenerationExecution,
    ContentGenerationPrompt,
    ContentGenerationResult,
    ContentGenerationService,
)
from .integrity import content_artifact_hash

__all__ = [
    "BriefClaim",
    "CarouselSlide",
    "ContentDraftArtifact",
    "ContentFormat",
    "ContentGenerationContext",
    "ContentGenerationExecution",
    "ContentGenerationOutput",
    "ContentGenerationPrompt",
    "ContentGenerationResult",
    "ContentGenerationService",
    "ContentPlatform",
    "ContentStyleConfig",
    "ContentStyleConfigLoader",
    "ContentStyleDefaults",
    "ContentTarget",
    "ContentVariantArtifact",
    "EditorialBrief",
    "InstagramCarouselContent",
    "build_editorial_brief",
    "content_artifact_hash",
]
