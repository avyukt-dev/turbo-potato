"""Stage-21 factual-boundary content generation package."""

from .attachment import (
    ContentMediaAttachmentService,
    MediaAttachmentConflict,
    MediaAttachmentRequest,
)
from .brief import build_editorial_brief
from .certainty import (
    CERTAINTY_POLICY_VERSION,
    CertaintyCeiling,
    ClaimAssertionStrength,
    ClaimPresentation,
    ClaimPresentationFrame,
    certainty_ceiling,
    presentation_violations,
)
from .claim_semantics import (
    ClaimSemanticPresentation,
    ClaimSemanticViolation,
    semantic_presentation_violations,
)
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
from .values import ClaimValueOccurrence, ClaimValuePresentation

__all__ = [
    "ClaimValuePresentation",
    "ClaimValueOccurrence",
    "ClaimSemanticPresentation",
    "ClaimSemanticViolation",
    "semantic_presentation_violations",
    "CERTAINTY_POLICY_VERSION",
    "ClaimAssertionStrength",
    "ClaimPresentation",
    "ClaimPresentationFrame",
    "CertaintyCeiling",
    "certainty_ceiling",
    "presentation_violations",
    "ContentMediaAttachmentService",
    "MediaAttachmentConflict",
    "MediaAttachmentRequest",
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
