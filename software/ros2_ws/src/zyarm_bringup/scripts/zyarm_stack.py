#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


STACK_SERVICE = "zyarm-stack.service"
BRIDGE_SERVICE = "foxglove-bridge.service"
ROS_SETUP = Path("/opt/ros/jazzy/setup.bash")
WORKSPACE_SETUP = Path("/home/frank/ZYArm-X1/software/ros2_ws/install/setup.bash")
SERIAL_DEVICE = Path("/dev/ttyUSB0")
CAMERA_DEVICE = Path("/dev/v4l/by-id/usb-XHH-260128-A_2M-video-index0")
STACK_PROBE = Path(
    "/home/frank/ZYArm-X1/software/ros2_ws/install/zyarm_bringup/"
    "lib/zyarm_bringup/stack_probe"
)
VENV_ROOT = Path(os.environ.get("ZYARM_VENV", "/home/frank/venv"))
VENV_PYTHON = VENV_ROOT / "bin/python3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zyarm-stack",
        description="Manage the ZYArm real control, Foxglove, and camera stack.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("start", "stop", "restart"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument(
            "--confirm-safe",
            action="store_true",
            help="confirm the arm is supported and the workspace is clear",
        )

    subparsers.add_parser("status")
    logs = subparsers.add_parser("logs")
    logs.add_argument("-f", "--follow", action="store_true")
    logs.add_argument("-n", "--lines", type=int, default=100)

    return parser


def require_safe_confirmation(args: argparse.Namespace) -> None:
    if not args.confirm_safe:
        raise RuntimeError(
            "Refusing real-arm state change without --confirm-safe. "
            "Support the arm, clear the workspace, and keep people away first."
        )


def run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, text=True)


def sudo_systemctl(*arguments: str) -> None:
    run(["sudo", "-n", "systemctl", *arguments])


def validate_start_prerequisites() -> None:
    missing = [
        str(path)
        for path in (
            ROS_SETUP,
            WORKSPACE_SETUP,
            VENV_PYTHON,
            SERIAL_DEVICE,
            CAMERA_DEVICE,
        )
        if not path.exists()
    ]
    if missing:
        raise RuntimeError("Missing required path(s): " + ", ".join(missing))


def ros_command(command: str, timeout: int = 12) -> subprocess.CompletedProcess:
    shell_command = (
        f"source {ROS_SETUP}; source {WORKSPACE_SETUP}; "
        f"timeout {timeout} {command}"
    )
    return subprocess.run(
        ["bash", "-lc", shell_command],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def service_state(service: str) -> str:
    result = subprocess.run(
        ["systemctl", "is-active", service],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return result.stdout.strip() or "unknown"


def query_stack_interfaces(
    timeout: int = 8,
) -> tuple[subprocess.CompletedProcess, bool, bool, bool, bool]:
    probe = ros_command(
        f"{VENV_PYTHON} {STACK_PROBE} --timeout {max(timeout, 1)}",
        timeout=max(timeout, 1) + 3,
    )
    try:
        payload = json.loads(probe.stdout.splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        payload = {}
    controller_states = payload.get("controller_states", {})
    controller_output = "\n".join(
        f"{name}: {state}" for name, state in sorted(controller_states.items())
    )
    controllers = subprocess.CompletedProcess(
        args=probe.args,
        returncode=probe.returncode,
        stdout=controller_output or probe.stdout,
    )
    controllers_available = bool(payload.get("controllers_ready", False))
    controllers_active = bool(payload.get("controllers_active", False))
    modes_ready = bool(payload.get("services_ready", False))
    video_ready = bool(payload.get("video_ready", False))
    return (
        controllers,
        controllers_available,
        controllers_active,
        modes_ready,
        video_ready,
    )


def print_status() -> int:
    stack_state = service_state(STACK_SERVICE)
    bridge_state = service_state(BRIDGE_SERVICE)
    print(f"{BRIDGE_SERVICE}: {bridge_state}")
    print(f"{STACK_SERVICE}: {stack_state}")
    print(f"serial: {'present' if SERIAL_DEVICE.exists() else 'missing'} ({SERIAL_DEVICE})")
    print(f"camera: {'present' if CAMERA_DEVICE.exists() else 'missing'} ({CAMERA_DEVICE})")

    if stack_state != "active":
        print("controllers: unavailable")
        print("mode services: unavailable")
        print("video: unavailable")
        return 1

    (
        controllers,
        controllers_available,
        controllers_active,
        modes_ready,
        video_ready,
    ) = query_stack_interfaces()
    print("controllers:")
    print(controllers.stdout.strip() or "unavailable")
    if controllers_active:
        print("control state: active")
    elif controllers_available:
        print("control state: recovery-ready (call /zyarm/reset to activate)")
    else:
        print("control state: incomplete")
    print("mode services: available" if modes_ready else "mode services: unavailable")
    print(
        "video: available (/camera/image/compressed)"
        if video_ready
        else "video: unavailable"
    )
    ready = controllers_available and modes_ready and video_ready
    return 0 if ready and bridge_state == "active" else 1


def wait_until_ready(timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    stack_state = service_state(STACK_SERVICE)
    while stack_state != "active" and time.monotonic() < deadline:
        time.sleep(0.25)
        stack_state = service_state(STACK_SERVICE)

    remaining = max(1, round(deadline - time.monotonic()))
    if stack_state != "active":
        raise RuntimeError(
            f"ZYArm systemd service did not become active; state={stack_state}"
        )

    (
        controllers,
        controllers_available,
        _,
        modes_ready,
        video_ready,
    ) = query_stack_interfaces(timeout=remaining)
    if controllers_available and modes_ready and video_ready:
        return
    raise RuntimeError(
        "ZYArm ROS interfaces did not become ready; "
        f"controllers={controllers_available}, mode_services={modes_ready}, "
        f"video={video_ready}, probe={controllers.stdout.strip() or 'no output'}"
    )


def initialize_fallback_parameters() -> None:
    request = (
        "{parameters: ["
        "{name: arm_controller.fallback_controllers, "
        "value: {type: 9, string_array_value: []}}, "
        "{name: gripper_controller.fallback_controllers, "
        "value: {type: 9, string_array_value: []}}, "
        "{name: joint_state_broadcaster.fallback_controllers, "
        "value: {type: 9, string_array_value: []}}]}"
    )
    result = ros_command(
        "ros2 service call "
        "/zyarm_x1_standard_controller_manager/set_parameters "
        f"rcl_interfaces/srv/SetParameters '{request}'",
        timeout=8,
    )
    if result.returncode != 0 or result.stdout.count("successful=True") != 3:
        raise RuntimeError(
            "Failed to initialize controller fallback parameters: "
            + result.stdout.strip()
        )


def start_stack() -> int:
    validate_start_prerequisites()
    print("Starting Foxglove and the recovery-ready arm stack...", flush=True)
    sudo_systemctl("start", BRIDGE_SERVICE)
    sudo_systemctl("start", STACK_SERVICE)
    try:
        print("Waiting for controllers, mode services, and video...", flush=True)
        wait_until_ready()
        initialize_fallback_parameters()
    except RuntimeError:
        sudo_systemctl("stop", STACK_SERVICE)
        raise
    print("ZYArm stack is ready. Run 'zyarm-stack status' for details.")
    return 0


def stop_stack() -> int:
    sudo_systemctl("stop", STACK_SERVICE)
    print_status()
    return 0 if service_state(STACK_SERVICE) == "inactive" else 1


def restart_stack() -> int:
    validate_start_prerequisites()
    print("Restarting Foxglove and the recovery-ready arm stack...", flush=True)
    sudo_systemctl("restart", BRIDGE_SERVICE)
    sudo_systemctl("restart", STACK_SERVICE)
    try:
        print("Waiting for controllers, mode services, and video...", flush=True)
        wait_until_ready()
        initialize_fallback_parameters()
    except RuntimeError:
        sudo_systemctl("stop", STACK_SERVICE)
        raise
    print("ZYArm stack is ready. Run 'zyarm-stack status' for details.")
    return 0


def show_logs(*, follow: bool, lines: int) -> int:
    command = [
        "sudo",
        "-n",
        "journalctl",
        "-u",
        STACK_SERVICE,
        "-u",
        BRIDGE_SERVICE,
        "--no-pager",
        "-n",
        str(max(lines, 1)),
    ]
    if follow:
        command.append("--follow")
    return subprocess.call(command)


def terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.send_signal(signal.SIGINT)


def run_daemon() -> int:
    commands = [
        [
            "ros2",
            "launch",
            "zyarm_bringup",
            "bringup_x1_standard_real_ros2_control.launch.py",
            "use_rviz:=false",
            "serial_port:=/dev/ttyUSB0",
        ],
        ["ros2", "launch", "zyarm_bringup", "foxglove_camera.launch.py"],
    ]
    processes: list[subprocess.Popen] = []
    stopping = False

    def handle_stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True
        for process in processes:
            terminate_process(process)

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    try:
        processes = [subprocess.Popen(command) for command in commands]
        while True:
            for process in processes:
                return_code = process.poll()
                if return_code is not None:
                    if stopping:
                        return 0
                    print(
                        f"Managed process exited with status {return_code}: "
                        + " ".join(process.args),
                        file=sys.stderr,
                    )
                    return return_code or 1
            time.sleep(0.25)
    finally:
        for process in processes:
            terminate_process(process)
        deadline = time.monotonic() + 10.0
        for process in processes:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                process.kill()
        for process in processes:
            process.wait()


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["--systemd-daemon"]:
        return run_daemon()
    args = build_parser().parse_args(arguments)
    try:
        if args.command in {"start", "stop", "restart"}:
            require_safe_confirmation(args)
        if args.command == "start":
            return start_stack()
        if args.command == "stop":
            return stop_stack()
        if args.command == "restart":
            return restart_stack()
        if args.command == "status":
            return print_status()
        if args.command == "logs":
            return show_logs(follow=args.follow, lines=args.lines)
        raise RuntimeError(f"Unsupported command: {args.command}")
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"zyarm-stack: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
