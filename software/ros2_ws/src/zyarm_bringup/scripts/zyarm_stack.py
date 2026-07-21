#!/usr/bin/env python3

from __future__ import annotations

import argparse
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
        for path in (ROS_SETUP, WORKSPACE_SETUP, SERIAL_DEVICE, CAMERA_DEVICE)
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
) -> tuple[subprocess.CompletedProcess, bool, bool, bool]:
    controllers = ros_command(
        "ros2 control list_controllers -c /zyarm_x1_standard_controller_manager",
        timeout=timeout,
    )
    expected_controllers = (
        "arm_controller",
        "gripper_controller",
        "joint_state_broadcaster",
    )
    controllers_ready = controllers.returncode == 0 and all(
        any(
            line.startswith(name) and line.rstrip().endswith("active")
            for line in controllers.stdout.splitlines()
        )
        for name in expected_controllers
    )
    topics = ros_command(
        "ros2 topic list | grep -E '^/camera/image/compressed$'",
        timeout=timeout,
    )
    video_ready = topics.returncode == 0 and bool(topics.stdout.strip())
    modes = ros_command(
        "for service in standby unload resume reset; do "
        "ros2 service info /zyarm/$service | grep -q 'Services count: 1' || exit 1; "
        "done",
        timeout=timeout,
    )
    modes_ready = modes.returncode == 0
    return controllers, controllers_ready, modes_ready, video_ready


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

    controllers, controllers_ready, modes_ready, video_ready = query_stack_interfaces()
    print("controllers:")
    print(controllers.stdout.strip() or "unavailable")
    print("mode services: available" if modes_ready else "mode services: unavailable")
    print(
        "video: available (/camera/image/compressed)"
        if video_ready
        else "video: unavailable"
    )
    ready = controllers_ready and modes_ready and video_ready
    return 0 if ready and bridge_state == "active" else 1


def wait_until_ready(timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if service_state(STACK_SERVICE) != "active":
            time.sleep(0.5)
            continue
        _, controllers_ready, modes_ready, video_ready = query_stack_interfaces(
            timeout=4
        )
        if controllers_ready and modes_ready and video_ready:
            return
        time.sleep(0.5)
    raise RuntimeError("ZYArm stack did not become ready within 30 seconds")


def start_stack() -> int:
    validate_start_prerequisites()
    sudo_systemctl("start", BRIDGE_SERVICE)
    sudo_systemctl("start", STACK_SERVICE)
    try:
        wait_until_ready()
    except RuntimeError:
        sudo_systemctl("stop", STACK_SERVICE)
        raise
    return print_status()


def stop_stack() -> int:
    sudo_systemctl("stop", STACK_SERVICE)
    print_status()
    return 0 if service_state(STACK_SERVICE) == "inactive" else 1


def restart_stack() -> int:
    validate_start_prerequisites()
    sudo_systemctl("restart", BRIDGE_SERVICE)
    sudo_systemctl("restart", STACK_SERVICE)
    try:
        wait_until_ready()
    except RuntimeError:
        sudo_systemctl("stop", STACK_SERVICE)
        raise
    return print_status()


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
