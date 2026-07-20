#!/usr/bin/env python3

from __future__ import annotations

import os
import re
import subprocess
import threading
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CompressedImage


def parse_capture_source(device: str) -> str:
    value = device.strip()
    if re.fullmatch(r"[+-]?\d+", value):
        return f"/dev/video{int(value)}"
    return value


class MjpegFrameParser:
    def __init__(self, max_buffer_bytes: int = 16 * 1024 * 1024) -> None:
        self._buffer = bytearray()
        self._max_buffer_bytes = max_buffer_bytes

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buffer.extend(chunk)
        frames = []
        while True:
            start = self._buffer.find(b"\xff\xd8")
            if start < 0:
                self._buffer[:] = self._buffer[-1:]
                break
            end = self._buffer.find(b"\xff\xd9", start + 2)
            if end < 0:
                if start > 0:
                    del self._buffer[:start]
                if len(self._buffer) > self._max_buffer_bytes:
                    self._buffer.clear()
                    raise ValueError("MJPEG frame exceeded the parser buffer limit")
                break
            frames.append(bytes(self._buffer[start : end + 2]))
            del self._buffer[: end + 2]
        return frames


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
        self.declare_parameter("fps", 30.0)
        self.declare_parameter("fourcc", "MJPG")
        self.declare_parameter("reconnect_interval_sec", 2.0)

        self._device = parse_capture_source(str(self.get_parameter("device").value))
        self._topic = str(self.get_parameter("topic").value)
        self._frame_id = str(self.get_parameter("frame_id").value)
        self._width = int(self.get_parameter("width").value)
        self._height = int(self.get_parameter("height").value)
        self._fps = float(self.get_parameter("fps").value)
        self._fourcc = str(self.get_parameter("fourcc").value)
        self._reconnect_interval = float(
            self.get_parameter("reconnect_interval_sec").value
        )
        self._validate_parameters()

        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        self._publisher = self.create_publisher(CompressedImage, self._topic, qos)
        self._stop_event = threading.Event()
        self._process_lock = threading.Lock()
        self._capture_process = None
        self._last_error_at = 0.0
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="zyarm-native-mjpeg-capture",
            daemon=True,
        )
        self._capture_thread.start()

        self.get_logger().info(
            f"Native MJPEG source={self._device}, topic={self._topic}, "
            f"size={self._width}x{self._height}, requested_fps={self._fps:g}"
        )

    def _validate_parameters(self) -> None:
        if self._width <= 0 or self._height <= 0:
            raise ValueError("width and height must be positive")
        if self._fps <= 0.0:
            raise ValueError("fps must be positive")
        if self._fourcc != "MJPG":
            raise ValueError("The native camera streamer requires fourcc=\"MJPG\"")
        if self._reconnect_interval <= 0.0:
            raise ValueError("reconnect_interval_sec must be positive")

    def _log_capture_error(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_error_at >= self._reconnect_interval:
            self.get_logger().error(message)
            self._last_error_at = now

    def _set_capture_process(self, process) -> None:
        with self._process_lock:
            self._capture_process = process

    def _stop_capture_process(self) -> None:
        with self._process_lock:
            process = self._capture_process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)

    def _capture_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                if not os.path.exists(self._device):
                    self._log_capture_error(
                        f"Camera device does not exist: {self._device}"
                    )
                    self._stop_event.wait(self._reconnect_interval)
                    continue

                command = [
                    "v4l2-ctl",
                    f"--device={self._device}",
                    (
                        "--set-fmt-video="
                        f"width={self._width},height={self._height},"
                        f"pixelformat={self._fourcc}"
                    ),
                    f"--set-parm={self._fps:g}",
                    "--stream-mmap=4",
                    "--stream-poll",
                    "--stream-count=0",
                    "--stream-to=-",
                ]
                try:
                    process = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        bufsize=0,
                    )
                except OSError as exc:
                    self._log_capture_error(f"Failed to start v4l2-ctl: {exc}")
                    self._stop_event.wait(self._reconnect_interval)
                    continue

                self._set_capture_process(process)
                parser = MjpegFrameParser()
                self.get_logger().info(
                    f"Native MJPEG stream opened at {self._width}x{self._height}"
                )
                try:
                    while not self._stop_event.is_set():
                        chunk = process.stdout.read(64 * 1024)
                        if not chunk:
                            break
                        for jpeg in parser.feed(chunk):
                            self._publish_jpeg(jpeg)
                except ValueError as exc:
                    self._log_capture_error(str(exc))
                finally:
                    self._stop_capture_process()
                    self._set_capture_process(None)

                if not self._stop_event.is_set():
                    self._log_capture_error("Native MJPEG stream stopped; reconnecting")
                    self._stop_event.wait(self._reconnect_interval)
        finally:
            self._stop_capture_process()
            self._set_capture_process(None)

    def _publish_jpeg(self, jpeg: bytes) -> None:
        message = CompressedImage()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = self._frame_id
        message.format = "jpeg"
        message.data = jpeg
        self._publisher.publish(message)

    def destroy_node(self):
        self._stop_event.set()
        self._stop_capture_process()
        self._capture_thread.join(timeout=2.0)
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
