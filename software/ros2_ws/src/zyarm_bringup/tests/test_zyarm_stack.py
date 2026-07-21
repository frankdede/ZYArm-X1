import json
from importlib.util import module_from_spec, spec_from_file_location
from inspect import getsource
from pathlib import Path
from subprocess import CompletedProcess

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
    assert "initialize_fallback_parameters" in source
    assert "string_array_value: []" in source


def test_systemd_unit_supervises_the_complete_stack():
    unit = (
        Path(__file__).parents[1]
        / "config"
        / "systemd"
        / "zyarm-stack.service"
    ).read_text(encoding="utf-8")

    assert "Requires=foxglove-bridge.service" in unit
    assert "KillMode=control-group" in unit
    assert "Restart=no" in unit


def test_fallback_parameters_are_sent_as_typed_empty_string_arrays(monkeypatch):
    module = _load_module()
    seen = []

    def fake_ros_command(command, timeout):
        seen.append((command, timeout))
        return CompletedProcess(
            args=command,
            returncode=0,
            stdout="successful=True\nsuccessful=True\nsuccessful=True\n",
        )

    monkeypatch.setattr(module, "ros_command", fake_ros_command)
    module.initialize_fallback_parameters()

    assert "type: 9" in seen[0][0]
    assert seen[0][0].count("string_array_value: []") == 3


def test_recovery_ready_inactive_controllers_are_accepted(monkeypatch):
    module = _load_module()

    def fake_ros_command(command, timeout):
        output = json.dumps(
            {
                "controller_states": {
                    "arm_controller": "inactive",
                    "gripper_controller": "inactive",
                    "joint_state_broadcaster": "inactive",
                },
                "controllers_ready": True,
                "controllers_active": False,
                "services_ready": True,
                "video_ready": True,
            }
        )
        return CompletedProcess(args=command, returncode=0, stdout=output)

    monkeypatch.setattr(module, "ros_command", fake_ros_command)
    _, available, active, modes, video = module.query_stack_interfaces()

    assert available
    assert not active
    assert modes
    assert video


def test_stack_probe_uses_one_persistent_ros_process():
    module = _load_module()
    source = (Path(__file__).parents[1] / "scripts" / "zyarm_stack.py").read_text(
        encoding="utf-8"
    )

    assert "stack_probe" in source
    assert "ThreadPoolExecutor" not in source
    assert "ros2 service info" not in source
    assert "print_status" not in getsource(module.start_stack)
    assert "print_status" not in getsource(module.restart_stack)
    assert "query_stack_interfaces(timeout=round(timeout))" in source


def test_stack_probe_is_installed():
    cmake = (Path(__file__).parents[1] / "CMakeLists.txt").read_text(
        encoding="utf-8"
    )

    assert "scripts/stack_probe.py" in cmake
    assert "RENAME stack_probe" in cmake


def test_real_controller_config_starts_hardware_inactive():
    config = (
        Path(__file__).parents[2]
        / "zyarm_control"
        / "config"
        / "zyarm_x1_standard_real_controllers.yaml"
    ).read_text(encoding="utf-8")

    assert "hardware_components_initial_state:" in config
    assert "ZyarmX1StandardSystem" in config
