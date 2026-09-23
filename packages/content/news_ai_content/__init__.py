"""Stage-21 factual-boundary content generation package."""

from .ai_projection import (
    AI_INPUT_PROJECTION_VERSION,
    CONTENT_GENERATION_MAX_CLAIMS,
    AIInputProjectionError,
    project_content_generation_input,
    project_quality_assessment_input,
)
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
from .media_generation import (
    MEDIA_GENERATION_PROMPT_CHECKSUM,
    MEDIA_GENERATION_PROMPT_VERSION,
    GeneratedMedia,
    MediaGenerationService,
)
from .media_storage import (
    GeneratedMediaStore,
    LocalGeneratedMediaStore,
    MediaStorageConfigurationError,
    MediaStorageError,
    MediaStorageIntegrityError,
    MediaStorageTransientError,
    S3GeneratedMediaStore,
    StoredMediaObject,
    StoreMediaRequest,
    build_s3_client,
    generated_media_storage_key,
)
from .values import ClaimValueOccurrence, ClaimValuePresentation

__all__ = [
    "AI_INPUT_PROJECTION_VERSION",
    "AIInputProjectionError",
    "CONTENT_GENERATION_MAX_CLAIMS",
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
    "MEDIA_GENERATION_PROMPT_VERSION",
    "MEDIA_GENERATION_PROMPT_CHECKSUM",
    "GeneratedMedia",
    "GeneratedMediaStore",
    "LocalGeneratedMediaStore",
    "MediaStorageConfigurationError",
    "MediaStorageError",
    "MediaStorageIntegrityError",
    "MediaStorageTransientError",
    "MediaGenerationService",
    "S3GeneratedMediaStore",
    "StoreMediaRequest",
    "StoredMediaObject",
    "build_s3_client",
    "generated_media_storage_key",
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
    "project_content_generation_input",
    "project_quality_assessment_input",
]
