"""Pure Instagram presentation rendering without HTTP, credentials, or factual rewriting."""

from __future__ import annotations

from .contracts import InstagramCarouselArtifact, InstagramCarouselRequest


class InstagramCarouselRenderer:
    def render(self, artifact: InstagramCarouselArtifact) -> InstagramCarouselRequest:
        """Copy an approved-shaped artifact into the strict transport command unchanged."""

        return InstagramCarouselRequest.model_validate(artifact.model_dump())
