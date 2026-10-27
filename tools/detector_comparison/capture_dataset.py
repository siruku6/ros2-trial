"""Gazebo のカメラ画像 1 枚と、そのときの物体の正しい位置を保存する。

検出モデルを比べるための画像を集めるのに使う。sim.launch.py を起動したあとに、
コンテナの中のシステムの Python（ROS 2 の入った方）で実行する。

    python3 tools/detector_comparison/capture_dataset.py /ws/tmp/dataset seed_003

Writes:
  - <出力先>/<名前>.png: カメラ画像
  - <出力先>/<名前>.json: カメラの内部パラメータと位置姿勢、物体の名前と正しい位置
"""

import argparse
import json
from pathlib import Path
import subprocess
import sys

import cv2
import message_filters
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import CameraInfo, Image
from tf2_ros import Buffer, TransformException, TransformListener

_REFERENCE_FRAME = 'crane_plus_base'
# crane_plus_with_table.launch.py が腕を置く高さ。Gazebo の世界座標から引いて
# crane_plus_base から見た高さにする [m]
_TABLE_TOP_HEIGHT = 1.015
_OBJECT_PREFIX = 'object_'
_WAIT_SECONDS = 15.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('output_dir', type=Path)
    parser.add_argument('name', help='保存するファイルの名前（拡張子なし）')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rclpy.init()
    node = _Capturer()
    try:
        sample = node.capture(_WAIT_SECONDS)
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
    if sample is None:
        sys.exit('カメラ画像か、カメラの位置を受け取れませんでした')

    rgb_image, metadata = sample
    metadata['objects'] = _read_object_positions()
    bgr_image = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2BGR)
    cv2.imwrite(str(args.output_dir / f'{args.name}.png'), bgr_image)
    (args.output_dir / f'{args.name}.json').write_text(json.dumps(metadata, indent=2))
    print(f'{args.name}: 物体 {len(metadata["objects"])} 個')


class _Capturer(Node):
    """画像とカメラの情報を 1 組受け取り、カメラの位置姿勢を tf から読む。"""

    def __init__(self) -> None:
        super().__init__('capture_dataset')
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)
        self._sample: tuple[np.ndarray, dict] | None = None
        image_subscriber = message_filters.Subscriber(self, Image, 'image_raw')
        info_subscriber = message_filters.Subscriber(self, CameraInfo, 'camera_info')
        self._synchronizer = message_filters.TimeSynchronizer(
            [image_subscriber, info_subscriber], 10
        )
        self._synchronizer.registerCallback(self._on_camera)

    def capture(self, timeout_seconds: float) -> tuple[np.ndarray, dict] | None:
        """1 組受け取るまで待ち、RGB の画像と、カメラの情報をまとめた辞書を返す。"""
        deadline = self.get_clock().now().nanoseconds + int(timeout_seconds * 1e9)
        while self._sample is None and self.get_clock().now().nanoseconds < deadline:
            rclpy.spin_once(self, timeout_sec=0.1)
        return self._sample

    def _on_camera(self, image_message: Image, info_message: CameraInfo) -> None:
        if self._sample is not None:
            return
        try:
            camera_pose = self._tf_buffer.lookup_transform(
                _REFERENCE_FRAME, image_message.header.frame_id, Time()
            )
        except TransformException:
            return
        channels = np.frombuffer(image_message.data, dtype=np.uint8).reshape(
            image_message.height, image_message.width, -1
        )
        if image_message.encoding == 'rgb8':
            rgb_image = channels[:, :, :3]
        else:
            rgb_image = channels[:, :, 2::-1]
        rotation = camera_pose.transform.rotation
        translation = camera_pose.transform.translation
        self._sample = (
            np.ascontiguousarray(rgb_image),
            {
                'camera_matrix': np.array(info_message.k).reshape(3, 3).tolist(),
                'camera_frame': image_message.header.frame_id,
                'camera_rotation_xyzw': [rotation.x, rotation.y, rotation.z, rotation.w],
                'camera_translation': [translation.x, translation.y, translation.z],
            },
        )


def _read_object_positions() -> list[dict]:
    """Gazebo から、名前が object_ で始まるモデルの位置を crane_plus_base から見た値で読む。"""
    output = subprocess.run(
        ['gz', 'topic', '-e', '-n', '1', '-t', '/world/default/pose/info'],
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout
    objects: list[dict] = []
    name: str | None = None
    position: dict[str, float] = {}
    in_position = False
    for line in output.splitlines():
        text = line.strip()
        if text.startswith('name: "'):
            name = text.split('"')[1]
            position = {}
        elif text.startswith('position {'):
            in_position = True
        elif in_position and text == '}':
            in_position = False
            # モデルの中のリンクも同じ名前で続けて出ることがあるので、最初の 1 つだけ使う
            seen = {sample['label'] for sample in objects}
            if name and name.startswith(_OBJECT_PREFIX) and name[len(_OBJECT_PREFIX):] not in seen:
                objects.append({
                    'label': name[len(_OBJECT_PREFIX):],
                    'x': position.get('x', 0.0),
                    'y': position.get('y', 0.0),
                    'z': position.get('z', 0.0) - _TABLE_TOP_HEIGHT,
                })
            name = None
        elif in_position and ':' in text:
            key, value = text.split(':', 1)
            position[key.strip()] = float(value)
    return objects


if __name__ == '__main__':
    main()
