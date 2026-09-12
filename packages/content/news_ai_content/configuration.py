"""Content-facing access to the editorial-owned content-style contract."""

from news_ai_common.config import ConfigDomain, ConfigLoader
from news_ai_editorial import ContentStyleConfig, ContentStyleDefaults, ContentStyleTarget


class ContentStyleConfigLoader:
    def __init__(self, loader: ConfigLoader) -> None:
        self.loader = loader

    def load(self) -> ContentStyleConfig:
        return self.loader.load_domain_file(
            ConfigDomain.EDITORIAL, "content-style.yaml", ContentStyleConfig
        )


__all__ = [
    "ContentStyleConfig",
    "ContentStyleConfigLoader",
    "ContentStyleDefaults",
    "ContentStyleTarget",
]
