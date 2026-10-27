from crane_sorter.object_detector import _to_rgb_array, ArmMotionMonitor
import numpy as np
from sensor_msgs.msg import Image


def _image_message(encoding: str, pixels: np.ndarray, padding: int = 0) -> Image:
    height, width = pixels.shape[:2]
    rows = np.zeros((height, width * 3 + padding), dtype=np.uint8)
    rows[:, :width * 3] = pixels.reshape(height, width * 3)
    return Image(
        height=height, width=width, encoding=encoding, step=width * 3 + padding,
        data=rows.tobytes(),
    )


def test_to_rgb_array_keeps_rgb8_and_drops_row_padding() -> None:
    pixels = np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3)
    assert np.array_equal(_to_rgb_array(_image_message('rgb8', pixels, padding=2)), pixels)


def test_to_rgb_array_swaps_bgr8_channels() -> None:
    pixels = np.array([[[1, 2, 3]]], dtype=np.uint8)
    assert _to_rgb_array(_image_message('bgr8', pixels)).tolist() == [[[3, 2, 1]]]


def test_arm_motion_monitor_waits_until_arm_has_been_still() -> None:
    monitor = ArmMotionMonitor(threshold=0.002, still_seconds=0.5)
    assert not monitor.is_moving(np.array([0.0, 0.0, 0.3]), now=0.0)
    # 1 cm 動いた
    assert monitor.is_moving(np.array([0.01, 0.0, 0.3]), now=1.0)
    # 止まって 0.3 秒ではまだ動いているとみなし、0.6 秒たてば止まったとみなす
    assert monitor.is_moving(np.array([0.01, 0.0, 0.3]), now=1.3)
    assert not monitor.is_moving(np.array([0.01, 0.0, 0.3]), now=1.6)


def test_arm_motion_monitor_ignores_small_jitter() -> None:
    monitor = ArmMotionMonitor(threshold=0.002, still_seconds=0.5)
    monitor.is_moving(np.array([0.0, 0.0, 0.3]), now=0.0)
    assert not monitor.is_moving(np.array([0.001, 0.0, 0.3]), now=1.0)
