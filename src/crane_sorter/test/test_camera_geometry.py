import math

from crane_sorter.camera_geometry import locate_on_plane, pixel_to_ray, rotate
import numpy as np
import pytest

_CAMERA_MATRIX = np.array([[500.0, 0.0, 320.0], [0.0, 500.0, 240.0], [0.0, 0.0, 1.0]])
# 光学座標系の z（前）を基準の座標系の -z（下）に向けた姿勢。x 軸まわりに 180 度回す
_LOOKING_DOWN = np.array([1.0, 0.0, 0.0, 0.0])


def test_pixel_at_image_center_looks_straight_ahead() -> None:
    assert pixel_to_ray(_CAMERA_MATRIX, (320.0, 240.0)) == pytest.approx([0.0, 0.0, 1.0])


def test_rotate_by_quarter_turn_about_z() -> None:
    half = math.radians(90.0) / 2
    quaternion = np.array([0.0, 0.0, math.sin(half), math.cos(half)])
    assert rotate(quaternion, np.array([1.0, 0.0, 0.0])) == pytest.approx([0.0, 1.0, 0.0])


def test_locate_on_plane_below_downward_camera() -> None:
    ray = pixel_to_ray(_CAMERA_MATRIX, (370.0, 240.0))
    point = locate_on_plane(ray, _LOOKING_DOWN, np.array([0.0, 0.0, 0.5]), plane_height=0.0)
    # 0.5 m 先の平面で、画像の中心から 50 画素右は 0.05 m 右
    assert point == pytest.approx([0.05, 0.0, 0.5])


def test_locate_on_plane_returns_none_when_ray_points_up() -> None:
    ray = pixel_to_ray(_CAMERA_MATRIX, (320.0, 240.0))
    assert locate_on_plane(ray, np.array([0.0, 0.0, 0.0, 1.0]), np.zeros(3), 0.0) is None
