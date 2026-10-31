"""画素に写った物体の 3 次元の位置を、カメラの位置姿勢と物体の高さから求める。

AI 版の物体を探すノードは、NumPy 2 の入った仮想環境で動くので、Jazzy の image_geometry と
SciPy を使わず、NumPy だけで計算する。Gazebo のカメラにはレンズの歪みがないので、
内部パラメータ（焦点距離と画像の中心）だけで視線の向きが決まる。
"""

import numpy as np


def pixel_to_ray(camera_matrix: np.ndarray, pixel: tuple[float, float]) -> np.ndarray:
    """画素を通る視線の向きを、カメラの光学座標系（z が前、x が右、y が下）で返す。

    Parameters
    ----------
    camera_matrix : np.ndarray
        CameraInfo の k を 3x3 に並べた内部パラメータ。
    pixel : tuple[float, float]
        画素の位置 (u, v)。

    Returns
    -------
    np.ndarray
        z 成分を 1 にした視線の向き。
    """
    focal_x, focal_y = camera_matrix[0, 0], camera_matrix[1, 1]
    center_x, center_y = camera_matrix[0, 2], camera_matrix[1, 2]
    return np.array([(pixel[0] - center_x) / focal_x, (pixel[1] - center_y) / focal_y, 1.0])


def rotate(quaternion_xyzw: np.ndarray, vector: np.ndarray) -> np.ndarray:
    """四元数 (x, y, z, w) で表した回転で、ベクトルを回す。"""
    axis = np.asarray(quaternion_xyzw[:3], dtype=float)
    scalar = float(quaternion_xyzw[3])
    twice_cross = 2.0 * np.cross(axis, vector)
    return vector + scalar * twice_cross + np.cross(axis, twice_cross)


def locate_on_plane(
    ray_in_camera: np.ndarray,
    camera_rotation_xyzw: np.ndarray,
    camera_translation: np.ndarray,
    plane_height: float,
) -> np.ndarray | None:
    """視線が、基準の座標系の z = plane_height の平面と交わる点を、カメラの座標系で返す。

    Parameters
    ----------
    ray_in_camera : np.ndarray
        カメラの座標系での視線の向き（pixel_to_ray の結果）。
    camera_rotation_xyzw : np.ndarray
        基準の座標系から見たカメラの姿勢（四元数 x, y, z, w）。
    camera_translation : np.ndarray
        基準の座標系から見たカメラの位置 [m]。
    plane_height : float
        平面の高さ [m]。物体の写った部分の高さに合わせる。

    Returns
    -------
    np.ndarray | None
        交点のカメラの座標系での位置 [m]。視線が平面の方へ向いていなければ None。
    """
    ray_in_reference = rotate(camera_rotation_xyzw, ray_in_camera)
    if ray_in_reference[2] >= 0.0:
        return None
    distance = (plane_height - float(camera_translation[2])) / ray_in_reference[2]
    return distance * ray_in_camera
