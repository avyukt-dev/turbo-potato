"""Safe application errors for the human-review boundary."""


class ReviewError(RuntimeError):
    code = "REVIEW_ERROR"


class ReviewNotFoundError(ReviewError):
    code = "REVIEW_ARTIFACT_NOT_FOUND"


class UnsupportedArtifactError(ReviewError):
    code = "UNSUPPORTED_ARTIFACT_TYPE"


class ReviewConflictError(ReviewError):
    code = "REVIEW_CONFLICT"


class ReviewPreconditionError(ReviewConflictError):
    code = "REVIEW_PRECONDITION_FAILED"


class ReviewValidationError(ReviewError):
    code = "REVIEW_VALIDATION_ERROR"


class ReviewConfigurationError(ReviewError):
    code = "REVIEW_CONFIGURATION_ERROR"
