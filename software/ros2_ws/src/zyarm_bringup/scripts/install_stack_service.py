#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import tempfile
from pathlib import Path


DEFAULT_VENV = Path("/home/frank/venv")
SYSTEMD_UNIT = Path("/etc/systemd/system/zyarm-stack.service")
SYSTEMD_ENV = Path("/etc/default/zyarm-stack")
CLI_WRAPPER = Path("/usr/local/bin/zyarm-stack")


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "software/ros2_ws" / "README.md").is_file():
            return parent
    raise FileNotFoundError("Could not locate the ZYArm repository root")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install the venv-backed ZYArm systemd service and CLI wrapper."
    )
    parser.add_argument(
        "--venv",
        type=Path,
        default=Path(os.environ.get("ZYARM_VENV", DEFAULT_VENV)),
    )
    parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="do not install the bringup Python requirements into the venv",
    )
    parser.add_argument(
        "--enable",
        action="store_true",
        help="enable zyarm-stack.service at boot without starting it",
    )
    return parser


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def sudo_install_text(content: str, target: Path, mode: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as temporary:
        temporary.write(content)
        temporary.flush()
        run(["sudo", "-n", "install", "-m", mode, temporary.name, str(target)])


def install(args: argparse.Namespace) -> None:
    repo = _repo_root()
    package = repo / "software/ros2_ws/src/zyarm_bringup"
    workspace = repo / "software/ros2_ws"
    venv = args.venv.expanduser().resolve()
    python = venv / "bin/python3"
    if not python.is_file():
        raise FileNotFoundError(f"Venv Python does not exist: {python}")

    requirements = package / "config/venv-requirements.txt"
    if not args.skip_deps:
        run([str(python), "-m", "pip", "install", "-r", str(requirements)])

    installed_cli = (
        workspace / "install/zyarm_bringup/lib/zyarm_bringup/zyarm_stack"
    )
    if not installed_cli.is_file():
        raise FileNotFoundError(
            f"Build zyarm_bringup before installing the service: {installed_cli}"
        )

    unit_source = package / "config/systemd/zyarm-stack.service"
    run(
        [
            "sudo",
            "-n",
            "install",
            "-m",
            "0644",
            str(unit_source),
            str(SYSTEMD_UNIT),
        ]
    )
    sudo_install_text(f'ZYARM_VENV="{venv}"\n', SYSTEMD_ENV, "0644")

    wrapper = (
        "#!/bin/sh\n"
        f"export ZYARM_VENV={shlex.quote(str(venv))}\n"
        f"exec {shlex.quote(str(python))} {shlex.quote(str(installed_cli))} \"$@\"\n"
    )
    sudo_install_text(wrapper, CLI_WRAPPER, "0755")
    run(["sudo", "-n", "systemctl", "daemon-reload"])
    if args.enable:
        run(["sudo", "-n", "systemctl", "enable", "zyarm-stack.service"])

    print(f"Installed {SYSTEMD_UNIT}")
    print(f"Configured ZYARM_VENV={venv} in {SYSTEMD_ENV}")
    print(f"Installed venv-backed CLI wrapper at {CLI_WRAPPER}")
    print("The service was not started; use zyarm-stack start --confirm-safe.")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        install(args)
    except (FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        print(f"install_stack_service: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
