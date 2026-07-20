import importlib.util
from pathlib import Path

import numpy as np


SCRIPT = Path(__file__).parents[1] / "scripts" / "camera_streamer.py"
SPEC = importlib.util.spec_from_file_location("camera_streamer", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_parse_capture_source_accepts_index_or_stable_path():
    assert MODULE.parse_capture_source("0") == 0
    assert MODULE.parse_capture_source(" 12 ") == 12
    assert MODULE.parse_capture_source("/dev/video0") == "/dev/video0"
    assert (
        MODULE.parse_capture_source("/dev/v4l/by-id/example")
        == "/dev/v4l/by-id/example"
    )


def test_synthetic_frame_has_requested_shape_and_changes():
    first = MODULE.make_synthetic_frame(320, 240, 0)
    second = MODULE.make_synthetic_frame(320, 240, 5)

    assert first.shape == (240, 320, 3)
    assert first.dtype == np.uint8
    assert np.any(first)
    assert not np.array_equal(first, second)
