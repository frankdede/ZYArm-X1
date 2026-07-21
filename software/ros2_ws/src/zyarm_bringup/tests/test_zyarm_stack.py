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
    assert "EnvironmentFile=-/etc/default/zyarm-stack" in unit
    assert r'export VIRTUAL_ENV=\"$ZYARM_VENV\"' in unit
    assert r'export PATH=\"$ZYARM_VENV/bin:$PATH\"' in unit
    assert r'\"$ZYARM_VENV/bin/python3\"' in unit


def test_service_installer_configures_venv_unit_and_wrapper():
    package = Path(__file__).parents[1]
    installer = (package / "scripts/install_stack_service.py").read_text(
        encoding="utf-8"
    )
    requirements = (package / "config/venv-requirements.txt").read_text(
        encoding="utf-8"
    )

    assert "/etc/default/zyarm-stack" in installer
    assert "/usr/local/bin/zyarm-stack" in installer
    assert '"-m", "pip", "install"' in installer
    assert "PyYAML" in requirements
    assert "pytest==9.1.1" in requirements


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
                "temperature_topic_count": 9,
            }
        )
        return CompletedProcess(args=command, returncode=0, stdout=output)

    monkeypatch.setattr(module, "ros_command", fake_ros_command)
    _, available, active, modes, video, temperature_count = module.query_stack_interfaces()

    assert available
    assert not active
    assert modes
    assert video
    assert temperature_count == 9


def test_status_reports_all_motor_temperature_topics(monkeypatch, capsys):
    module = _load_module()
    monkeypatch.setattr(module, "service_state", lambda _service: "active")
    monkeypatch.setattr(
        module,
        "query_stack_interfaces",
        lambda: (
            CompletedProcess(args=[], returncode=0, stdout="gripper_controller: active"),
            True,
            True,
            True,
            True,
            9,
        ),
    )

    assert module.print_status() == 0
    assert "motor temperatures: available (9/9)" in capsys.readouterr().out


def test_stack_probe_uses_one_persistent_ros_process():
    module = _load_module()
    source = (Path(__file__).parents[1] / "scripts" / "zyarm_stack.py").read_text(
        encoding="utf-8"
    )

    assert "stack_probe" in source
    assert 'VENV_PYTHON = VENV_ROOT / "bin/python3"' in source
    assert "ThreadPoolExecutor" not in source
    assert "ros2 service info" not in source
    assert "print_status" not in getsource(module.start_stack)
    assert "print_status" not in getsource(module.restart_stack)
    assert "while stack_state != \"active\"" in source


def test_stack_probe_is_installed():
    cmake = (Path(__file__).parents[1] / "CMakeLists.txt").read_text(
        encoding="utf-8"
    )

    assert "scripts/stack_probe.py" in cmake
    assert "RENAME stack_probe" in cmake


def test_wait_until_ready_polls_systemd_before_ros(monkeypatch):
    module = _load_module()
    states = iter(["activating", "activating", "active"])
    probes = []

    monkeypatch.setattr(module, "service_state", lambda _service: next(states))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        module,
        "query_stack_interfaces",
        lambda timeout: probes.append(timeout)
        or (
            CompletedProcess(args=[], returncode=0, stdout="ready"),
            True,
            False,
            True,
            True,
            9,
        ),
    )

    module.wait_until_ready(timeout=30.0)

    assert probes
    assert probes[0] > 0


def test_real_controller_config_starts_hardware_inactive():
    config = (
        Path(__file__).parents[2]
        / "zyarm_control"
        / "config"
        / "zyarm_x1_standard_real_controllers.yaml"
    ).read_text(encoding="utf-8")

    assert "hardware_components_initial_state:" in config
    assert "ZyarmX1StandardSystem" in config
