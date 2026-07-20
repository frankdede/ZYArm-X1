#!/usr/bin/env python3

from __future__ import annotations

import os
import re
import time

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CompressedImage


def parse_capture_source(device: str):
    value = device.strip()
    if re.fullmatch(r"[+-]?\d+", value):
        return int(value)
    return value


def make_synthetic_frame(width: int, height: int, frame_index: int) -> np.ndarray:
    frame = np.full((height, width, 3), (32, 36, 40), dtype=np.uint8)
    bar_width = max(12, width // 18)
    bar_x = (frame_index * max(2, width // 120)) % (width + bar_width) - bar_width
    cv2.rectangle(frame, (bar_x, 0), (bar_x + bar_width, height), (30, 190, 235), -1)
    cv2.putText(
        frame,
        "ZYArm-X1 camera test",
        (max(12, width // 32), max(36, height // 6)),
        cv2.FONT_HERSHEY_SIMPLEX,
        max(0.5, min(width, height) / 700.0),
        (245, 245, 245),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        frame,
        f"frame {frame_index}",
        (max(12, width // 32), max(72, height // 3)),
        cv2.FONT_HERSHEY_SIMPLEX,
        max(0.45, min(width, height) / 850.0),
        (180, 210, 220),
        1,
        cv2.LINE_AA,
    )
    return frame


class CameraStreamer(Node):
    def __init__(self) -> None:
        super().__init__("zyarm_camera_streamer")
        self.declare_parameter(
            "device",
            "/dev/v4l/by-id/usb-XHH-260128-A_2M-video-index0",
        )
        self.declare_parameter("topic", "/camera/image/compressed")
        self.declare_parameter("frame_id", "camera_link")
        self.declare_parameter("width", 640)
        self.declare_parameter("height", 480)
        self.declare_parameter("fps", 15.0)
        self.declare_parameter("fourcc", "MJPG")
        self.declare_parameter("jpeg_quality", 80)
        self.declare_parameter("reconnect_interval_sec", 2.0)
        self.declare_parameter("synthetic", False)

        self._device = str(self.get_parameter("device").value)
        self._topic = str(self.get_parameter("topic").value)
        self._frame_id = str(self.get_parameter("frame_id").value)
        self._width = int(self.get_parameter("width").value)
        self._height = int(self.get_parameter("height").value)
        self._fps = float(self.get_parameter("fps").value)
        self._fourcc = str(self.get_parameter("fourcc").value)
        self._jpeg_quality = int(self.get_parameter("jpeg_quality").value)
        self._reconnect_interval = float(
            self.get_parameter("reconnect_interval_sec").value
        )
        self._synthetic = bool(self.get_parameter("synthetic").value)
        self._validate_parameters()

        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        self._publisher = self.create_publisher(CompressedImage, self._topic, qos)
        self._capture = None
        self._next_open_at = 0.0
        self._last_error_at = 0.0
        self._frame_index = 0
        self._timer = self.create_timer(1.0 / self._fps, self._publish_frame)

        source = "synthetic frames" if self._synthetic else self._device
        self.get_logger().info(
            f"Camera stream source={source}, topic={self._topic}, "
            f"size={self._width}x{self._height}, publish_fps={self._fps:g}"
        )

    def _validate_parameters(self) -> None:
        if self._width <= 0 or self._height <= 0:
            raise ValueError("width and height must be positive")
        if self._fps <= 0.0:
            raise ValueError("fps must be positive")
        if len(self._fourcc) not in {0, 4}:
            raise ValueError("fourcc must be empty or exactly four characters")
        if not 1 <= self._jpeg_quality <= 100:
            raise ValueError("jpeg_quality must be between 1 and 100")
        if self._reconnect_interval <= 0.0:
            raise ValueError("reconnect_interval_sec must be positive")

    def _log_capture_error(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_error_at >= self._reconnect_interval:
            self.get_logger().error(message)
            self._last_error_at = now

    def _open_capture(self) -> bool:
        if self._capture is not None and self._capture.isOpened():
            return True

        now = time.monotonic()
        if now < self._next_open_at:
            return False
        self._next_open_at = now + self._reconnect_interval

        source = parse_capture_source(self._device)
        if isinstance(source, str) and source.startswith("/dev/") and not os.path.exists(source):
            self._log_capture_error(f"Camera device does not exist: {source}")
            return False

        capture = cv2.VideoCapture(source, cv2.CAP_V4L2)
        if self._fourcc:
            capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self._fourcc))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        capture.set(cv2.CAP_PROP_FPS, self._fps)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not capture.isOpened():
            capture.release()
            self._log_capture_error(f"Failed to open camera device: {self._device}")
            return False

        self._capture = capture
        actual_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = capture.get(cv2.CAP_PROP_FPS)
        self.get_logger().info(
            f"Camera opened at {actual_width}x{actual_height} @ {actual_fps:g}fps"
        )
        return True

    def _read_frame(self):
        if self._synthetic:
            return make_synthetic_frame(
                self._width, self._height, self._frame_index
            )
        if not self._open_capture():
            return None

        ok, frame = self._capture.read()
        if ok:
            return frame

        self._capture.release()
        self._capture = None
        self._next_open_at = time.monotonic() + self._reconnect_interval
        self._log_capture_error("Camera frame read failed; reconnecting")
        return None

    def _publish_frame(self) -> None:
        frame = self._read_frame()
        if frame is None:
            return

        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality],
        )
        if not ok:
            self._log_capture_error("JPEG encoding failed")
            return

        message = CompressedImage()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = self._frame_id
        message.format = "jpeg"
        message.data = encoded.tobytes()
        self._publisher.publish(message)
        self._frame_index += 1

    def destroy_node(self):
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = CameraStreamer()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
