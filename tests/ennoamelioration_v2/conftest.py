import pytest

from agents.EnnoAmeliorationV2.config import Config


@pytest.fixture
def config(tmp_path):
    return Config(data_dir=tmp_path / "data", max_steps=6)
