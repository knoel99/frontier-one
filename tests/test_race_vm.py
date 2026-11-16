"""tools/race_vm.py: what the VM receives (no live call here; the live check is `tools/race_vm.py up --wait`)."""

import importlib.util
import pathlib
import tomllib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("race_vm", ROOT / "tools" / "race_vm.py")
race_vm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(race_vm)


def test_the_vm_mission_has_no_secrets_and_keeps_every_other_table():
    text = ('[mission]\nname = "m"\n[secrets]\nOPENAI_API_KEY = "file:/home/me/k"\n# note\n\n'
            '[grading]\ncriteria = ["a"]\n')
    m = tomllib.loads(race_vm.mission_for_vm(text))
    assert "secrets" not in m and m["mission"]["name"] == "m" and m["grading"]["criteria"] == ["a"]


def test_the_init_script_is_filled_completely_and_a_missing_value_is_refused():
    values = dict.fromkeys(["BRANCH", "MISSION_B64", "SUBJECT_NAME", "SUBJECT_B64", "SCW_KEY", "REGION", "PROJECT",
                            "SECRET", "ZONE", "TOKEN", "HOST"], "x")
    assert "__" not in race_vm.fill(race_vm.INIT.read_text(), values).replace("__future__", "")
    with pytest.raises(ValueError, match="TOKEN"):
        race_vm.fill(race_vm.INIT.read_text(), {k: v for k, v in values.items() if k != "TOKEN"})


def test_a_subject_outside_the_mission_folder_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        race_vm.subject_file(tmp_path / "m.toml", {"mission": {"subject": "../secret.pdf"}})
    assert race_vm.subject_file(tmp_path / "m.toml", {"mission": {"subject": "https://x.org/s.pdf"}}) == ("", b"")
