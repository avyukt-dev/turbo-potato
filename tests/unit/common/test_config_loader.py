from pathlib import Path

import pytest
from pydantic import BaseModel

from news_ai_common.config import ConfigDomain, ConfigError, ConfigLoader


class ExampleConfig(BaseModel):
    enabled: bool


def test_load_model_from_domain(tmp_path: Path) -> None:
    domain = tmp_path / "sources"
    domain.mkdir()
    (domain / "registry.yaml").write_text("enabled: true\n", encoding="utf-8")

    loader = ConfigLoader(tmp_path)
    result = loader.load_domain_file(ConfigDomain.SOURCES, "registry.yaml", ExampleConfig)

    assert result.enabled is True


def test_rejects_path_escape(tmp_path: Path) -> None:
    loader = ConfigLoader(tmp_path)

    with pytest.raises(ConfigError, match="escapes"):
        loader.load_yaml("../outside.yaml")


def test_rejects_non_mapping_yaml(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("- one\n- two\n", encoding="utf-8")
    loader = ConfigLoader(tmp_path)

    with pytest.raises(ConfigError, match="mapping"):
        loader.load_yaml("bad.yaml")
