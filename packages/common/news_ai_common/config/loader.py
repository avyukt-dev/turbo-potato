"""Safe YAML configuration loader.

Configuration ownership is enforced by directory layout. The loader is deliberately generic so
individual domain packages can provide their own Pydantic models without creating a second config
system.
"""

from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel

from .models import ConfigDomain

ModelT = TypeVar("ModelT", bound=BaseModel)


class ConfigError(RuntimeError):
    """Raised when configuration cannot be loaded safely."""


class ConfigLoader:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def _resolve(self, relative_path: str | Path) -> Path:
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ConfigError("configuration path escapes configured root") from exc
        if candidate.suffix.lower() not in {".yaml", ".yml"}:
            raise ConfigError("configuration files must be YAML")
        return candidate

    def load_yaml(self, relative_path: str | Path) -> dict[str, object]:
        path = self._resolve(relative_path)
        if not path.is_file():
            raise ConfigError(f"configuration file not found: {relative_path}")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigError(f"unable to load configuration: {relative_path}") from exc
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ConfigError("top-level YAML value must be a mapping")
        return raw

    def load_model(self, relative_path: str | Path, model: type[ModelT]) -> ModelT:
        return model.model_validate(self.load_yaml(relative_path))

    def load_domain_file(
        self,
        domain: ConfigDomain,
        filename: str,
        model: type[ModelT],
    ) -> ModelT:
        if "/" in filename or "\\" in filename:
            raise ConfigError("domain filename must not contain path separators")
        return self.load_model(Path(domain.value) / filename, model)
