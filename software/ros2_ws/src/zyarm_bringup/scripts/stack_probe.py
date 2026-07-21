#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import time

import rclpy
from controller_manager_msgs.srv import ListControllers
from rclpy.node import Node


EXPECTED_CONTROLLERS = {
    "arm_controller",
    "gripper_controller",
    "joint_state_broadcaster",
}
EXPECTED_SERVICES = {
    "/zyarm/standby",
    "/zyarm/unload",
    "/zyarm/resume",
    "/zyarm/reset",
}
VIDEO_TOPIC = "/camera/image/compressed"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    rclpy.init()
    node = Node("zyarm_stack_probe", enable_rosout=False)
    client = node.create_client(
        ListControllers,
        "/zyarm_x1_standard_controller_manager/list_controllers",
    )
    deadline = time.monotonic() + max(args.timeout, 0.1)
    future = None
    last_request_at = 0.0
    controller_states: dict[str, str] = {}
    services_ready = False
    video_ready = False

    try:
        while rclpy.ok() and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.2)
            now = time.monotonic()

            if future is not None and future.done():
                if future.exception() is None:
                    response = future.result()
                    controller_states = {
                        controller.name: controller.state
                        for controller in response.controller
                    }
                future = None

            if future is None and client.service_is_ready() and now - last_request_at >= 0.5:
                future = client.call_async(ListControllers.Request())
                last_request_at = now

            service_names = {
                name for name, _types in node.get_service_names_and_types()
            }
            topic_names = {name for name, _types in node.get_topic_names_and_types()}
            services_ready = EXPECTED_SERVICES.issubset(service_names)
            video_ready = VIDEO_TOPIC in topic_names
            controllers_ready = all(
                controller_states.get(name) in {"active", "inactive"}
                for name in EXPECTED_CONTROLLERS
            )
            if controllers_ready and services_ready and video_ready:
                break

        controllers_ready = all(
            controller_states.get(name) in {"active", "inactive"}
            for name in EXPECTED_CONTROLLERS
        )
        result = {
            "controller_states": controller_states,
            "controllers_ready": controllers_ready,
            "controllers_active": controllers_ready
            and all(
                controller_states.get(name) == "active"
                for name in EXPECTED_CONTROLLERS
            ),
            "services_ready": services_ready,
            "video_ready": video_ready,
        }
        print(json.dumps(result, sort_keys=True))
        return 0 if controllers_ready and services_ready and video_ready else 1
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
