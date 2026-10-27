from crane_sorter.object_detector import _to_rgb_array
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
