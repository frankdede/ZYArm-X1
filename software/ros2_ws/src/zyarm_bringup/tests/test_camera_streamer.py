import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "camera_streamer.py"
SPEC = importlib.util.spec_from_file_location("camera_streamer", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_parse_capture_source_accepts_index_or_stable_path():
    assert MODULE.parse_capture_source("0") == "/dev/video0"
    assert MODULE.parse_capture_source(" 12 ") == "/dev/video12"
    assert MODULE.parse_capture_source("/dev/video0") == "/dev/video0"
    assert (
        MODULE.parse_capture_source("/dev/v4l/by-id/example")
        == "/dev/v4l/by-id/example"
    )
def test_mjpeg_parser_accepts_fragmented_frames_and_discards_noise():
    parser = MODULE.MjpegFrameParser()
    first = b"\xff\xd8first\xff\xd9"
    second = b"\xff\xd8second\xff\xd9"

    assert parser.feed(b"noise" + first[:5]) == []
    assert parser.feed(first[5:] + second + b"tail") == [first, second]
