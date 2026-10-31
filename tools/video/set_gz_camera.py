"""Gazebo の画面の視点を、カメラの位置と注視点で指定して動かす。

コンテナの中で実行する。Gazebo の画面の CameraTracking プラグインのサービス
/gui/move_to/pose を呼ぶ。座標は Gazebo の世界座標（単位 m）。

    python3 tools/video/set_gz_camera.py 0.78 -0.12 1.45 0.08 0.0 1.06
"""

import argparse
import math
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ('x', 'y', 'z', 'target_x', 'target_y', 'target_z'):
        parser.add_argument(name, type=float)
    args = parser.parse_args()
    qx, qy, qz, qw = look_at_quaternion(
        (args.x, args.y, args.z), (args.target_x, args.target_y, args.target_z)
    )
    request = (
        f'pose: {{position: {{x: {args.x}, y: {args.y}, z: {args.z}}}, '
        f'orientation: {{x: {qx:.6f}, y: {qy:.6f}, z: {qz:.6f}, w: {qw:.6f}}}}}'
    )
    subprocess.run(
        [
            'gz', 'service', '-s', '/gui/move_to/pose',
            '--reqtype', 'gz.msgs.GUICamera', '--reptype', 'gz.msgs.Boolean',
            '--timeout', '3000', '--req', request,
        ],
        check=True,
    )


def look_at_quaternion(
    position: tuple[float, float, float], target: tuple[float, float, float]
) -> tuple[float, float, float, float]:
    """position から target を向く、横に傾かない向きを四元数 (x, y, z, w) で返す。"""
    dx, dy, dz = (t - p for t, p in zip(target, position))
    yaw = math.atan2(dy, dx)
    # Gazebo のカメラは x 軸の向きを見ていて、y 軸まわりの正の回転で下を向く
    pitch = math.atan2(-dz, math.hypot(dx, dy))
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    return (-sy * sp, cy * sp, sy * cp, cy * cp)


if __name__ == '__main__':
    main()
