"""Machine-local settings come from files; nothing has to be exported."""
import json

import pytest

from nixsci.lab import config, home


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SYSTEM", tmp_path / "etc")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "etc").mkdir()
    (tmp_path / "xdg" / "nixsci").mkdir(parents=True)
    return tmp_path


def test_without_any_file_a_setting_is_its_default(dirs):
    assert config.files("lab") == []
    assert config.setting("lab", "x", "d") == "d"


def test_the_users_file_wins_over_the_systems_and_each_key_is_independent(dirs):
    (dirs / "etc" / "lab.json").write_text(json.dumps({"x": "system", "y": "system"}))
    (dirs / "xdg" / "nixsci" / "lab.json").write_text(json.dumps({"x": "user"}))
    assert config.setting("lab", "x") == "user"
    assert config.setting("lab", "y") == "system"


def test_a_file_that_is_not_an_object_is_refused_naming_it(dirs):
    (dirs / "xdg" / "nixsci" / "lab.json").write_text("[]")
    with pytest.raises(ValueError, match="lab.json: expected a JSON object"):
        config.setting("lab", "x")


def test_the_input_store_is_in_the_home_directory_unless_a_file_says_where(dirs):
    assert home.inputs_root() == dirs / "home" / ".nixsci"
    (dirs / "etc" / "inputs.json").write_text(json.dumps({"path": "/shared/inputs"}))
    assert str(home.inputs_root()) == "/shared/inputs"
