from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


def _load_module():
    path = Path(__file__).parents[1] / "scripts" / "zyarm_stack.py"
    spec = spec_from_file_location("zyarm_stack", path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_state_changes_require_explicit_confirmation():
    module = _load_module()
    parser = module.build_parser()

    with pytest.raises(RuntimeError, match="--confirm-safe"):
        module.require_safe_confirmation(parser.parse_args(["start"]))

    module.require_safe_confirmation(parser.parse_args(["start", "--confirm-safe"]))


def test_daemon_manages_real_control_and_camera_launches():
    source = (Path(__file__).parents[1] / "scripts" / "zyarm_stack.py").read_text(
        encoding="utf-8"
    )

    assert "bringup_x1_standard_real_ros2_control.launch.py" in source
    assert "foxglove_camera.launch.py" in source
    assert "foxglove-bridge.service" in source
    assert "wait_until_ready" in source


def test_systemd_unit_supervises_the_complete_stack():
    unit = (
        Path(__file__).parents[1]
        / "config"
        / "systemd"
        / "zyarm-stack.service"
    ).read_text(encoding="utf-8")

    assert "Requires=foxglove-bridge.service" in unit
    assert "KillMode=control-group" in unit
    assert "Restart=on-failure" in unit
