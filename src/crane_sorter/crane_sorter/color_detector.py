# Copyright 2025 RT Corporation
# Copyright 2026 sirukufarios
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# rt-net/crane_plus（jazzy ブランチ、34c4584）の
# crane_plus_examples_py/crane_plus_examples_py/color_detection.py を元にしたファイル。
# 変更点:
#   - 赤 1 色だけでなく、パラメータで指定した複数の色の物体を、色ごとに 1 個ずつ探す
#   - 色の範囲を、コードの定数ではなくパラメータで受け取る
#   - 3 次元の位置を、カメラの高さを 0.46 m と決め打ちして求めるのをやめ、
#     tf で読んだカメラの位置姿勢から、視線と、物体の高さに合わせた平面との交点として求める
#   - 物体の座標系の名前を target_0 から object_<物体の名前> に変えた
#   - 見つけた物体に枠と名前を描いた画像を配信する
#   - 画像の端に接している（一部しか写っていない）かたまりは、位置がずれるので捨てる

import cv2
from cv_bridge import CvBridge
from geometry_msgs.msg import TransformStamped
from image_geometry import PinholeCameraModel
import message_filters
import numpy as np
import rclpy
from rclpy.node import Node
from scipy.spatial.transform import Rotation
from sensor_msgs.msg import CameraInfo, Image
from tf2_ros import Buffer, TransformBroadcaster, TransformException, TransformListener

# 枠を描く色（RGB）
_BOX_COLOR = (255, 255, 0)


class ColorDetector(Node):
    """カメラ画像から色で物体を探し、見つけた位置を tf の座標系として配信するノード。

    掴む・置くを実行するノードは、この座標系（object_<物体の名前>）を読んで物体の位置を知る。
    物体を探す方法を入れ替えても、同じ名前の座標系を配信すれば、掴む側は書き換えずに済む。
    """

    def __init__(self) -> None:
        super().__init__('color_detector')
        self._labels: list[str] = list(
            self.declare_parameter('labels', ['red_cube', 'green_cube', 'blue_cube']).value
        )
        self._hsv_ranges: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {
            label: _parse_hsv_ranges(
                self.declare_parameter(f'{label}.hsv_ranges', [0] * 6).value
            )
            for label in self._labels
        }
        self._min_area: int = self.declare_parameter('min_area', 300).value
        self._plane_height: float = self.declare_parameter('plane_height', 0.035).value
        self._reference_frame: str = self.declare_parameter(
            'reference_frame', 'crane_plus_base'
        ).value
        self._frame_prefix: str = self.declare_parameter('frame_prefix', 'object_').value

        # 画像とカメラの情報は同じ時刻のものを組にして受け取る
        image_subscriber = message_filters.Subscriber(self, Image, 'image_raw')
        info_subscriber = message_filters.Subscriber(self, CameraInfo, 'camera_info')
        self._synchronizer = message_filters.TimeSynchronizer(
            [image_subscriber, info_subscriber], 10
        )
        self._synchronizer.registerCallback(self._on_camera)

        self._detection_image_publisher = self.create_publisher(Image, '~/detections', 10)
        self._tf_broadcaster = TransformBroadcaster(self)
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)
        self._bridge = CvBridge()
        self._camera_model = PinholeCameraModel()

    def _on_camera(self, image_message: Image, info_message: CameraInfo) -> None:
        """画像 1 枚ごとに、各色の物体を探して tf と検出結果の画像を配信する。"""
        try:
            camera_pose = self._tf_buffer.lookup_transform(
                self._reference_frame, image_message.header.frame_id, rclpy.time.Time()
            )
        except TransformException as error:
            self.get_logger().warn(f'カメラの位置を読めません: {error}', throttle_duration_sec=5.0)
            return

        self._camera_model.fromCameraInfo(info_message)
        rgb_image = self._bridge.imgmsg_to_cv2(image_message, desired_encoding='rgb8')
        hsv_image = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2HSV)
        detection_image = rgb_image.copy()

        for label in self._labels:
            blob = _find_largest_blob(hsv_image, self._hsv_ranges[label], self._min_area)
            if blob is None:
                continue
            center, bounding_box = blob
            position = self._locate_on_plane(center, camera_pose)
            if position is None:
                continue
            self._broadcast_object(label, position, image_message)
            _draw_detection(detection_image, label, bounding_box)

        self._detection_image_publisher.publish(
            self._bridge.cv2_to_imgmsg(detection_image, encoding='rgb8')
        )

    def _locate_on_plane(
        self, pixel: tuple[float, float], camera_pose: TransformStamped
    ) -> np.ndarray | None:
        """画素に写った物体の 3 次元の位置を、カメラの座標系で求める。

        カメラから画素の方向に伸ばした視線が、基準の座標系で z = plane_height の
        平面と交わる点を返す。視線が平面と交わらないときは None を返す。
        """
        rectified = self._camera_model.rectifyPoint(pixel)
        ray_in_camera = np.array(self._camera_model.projectPixelTo3dRay(rectified))

        rotation = camera_pose.transform.rotation
        translation = camera_pose.transform.translation
        camera_to_reference = Rotation.from_quat(
            [rotation.x, rotation.y, rotation.z, rotation.w]
        )
        ray_in_reference = camera_to_reference.apply(ray_in_camera)
        if ray_in_reference[2] >= 0.0:
            return None
        distance = (self._plane_height - translation.z) / ray_in_reference[2]
        return distance * ray_in_camera

    def _broadcast_object(
        self, label: str, position_in_camera: np.ndarray, image_message: Image
    ) -> None:
        """見つけた物体の位置を、カメラの座標系から見た tf の座標系として配信する。"""
        transform = TransformStamped()
        transform.header = image_message.header
        transform.child_frame_id = f'{self._frame_prefix}{label}'
        transform.transform.translation.x = float(position_in_camera[0])
        transform.transform.translation.y = float(position_in_camera[1])
        transform.transform.translation.z = float(position_in_camera[2])
        transform.transform.rotation.w = 1.0
        self._tf_broadcaster.sendTransform(transform)


def _parse_hsv_ranges(flat_values: list[int]) -> list[tuple[np.ndarray, np.ndarray]]:
    """[H, S, V の下限, H, S, V の上限] を並べた一次元のリストを、範囲の組に分ける。

    赤のように色相が 0 と 179 の両端にまたがる色は、範囲を 2 つ並べて書く。
    """
    if len(flat_values) % 6 != 0:
        raise ValueError(f'hsv_ranges の要素数は 6 の倍数にしてください: {flat_values}')
    ranges: list[tuple[np.ndarray, np.ndarray]] = []
    for start in range(0, len(flat_values), 6):
        lower = np.array(flat_values[start:start + 3], dtype=np.uint8)
        upper = np.array(flat_values[start + 3:start + 6], dtype=np.uint8)
        ranges.append((lower, upper))
    return ranges


def _find_largest_blob(
    hsv_image: np.ndarray, hsv_ranges: list[tuple[np.ndarray, np.ndarray]], min_area: int
) -> tuple[tuple[float, float], tuple[int, int, int, int]] | None:
    """色の範囲に入る画素のかたまりのうち、最も大きいものの重心と外接矩形を返す。

    画像の端に接しているかたまりは、物体の一部しか写っておらず重心がずれるので、候補から除く。

    Parameters
    ----------
    hsv_image : np.ndarray
        HSV に変換した画像。
    hsv_ranges : list[tuple[np.ndarray, np.ndarray]]
        色の範囲（下限と上限）の組。どれか 1 つに入る画素を拾う。
    min_area : int
        かたまりとみなす最小の画素数。これより小さいものは雑音として捨てる。

    Returns
    -------
    tuple | None
        ((重心の x, 重心の y), (矩形の左, 上, 幅, 高さ))。見つからなければ None。
    """
    mask = np.zeros(hsv_image.shape[:2], dtype=np.uint8)
    for lower, upper in hsv_ranges:
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv_image, lower, upper))

    # 小さな雑音を消してから、かたまりの中の穴を埋める
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask)
    image_height, image_width = mask.shape
    # 0 番は背景なので除く
    candidates = [
        index for index in range(1, count)
        if stats[index, cv2.CC_STAT_AREA] >= min_area
        and not _touches_border(stats[index, :4], image_width, image_height)
    ]
    if not candidates:
        return None
    largest = max(candidates, key=lambda index: stats[index, cv2.CC_STAT_AREA])
    left, top, width, height = (int(value) for value in stats[largest, :4])
    center_x, center_y = centroids[largest]
    return (float(center_x), float(center_y)), (left, top, width, height)


def _touches_border(bounding_box: np.ndarray, image_width: int, image_height: int) -> bool:
    """外接矩形（左, 上, 幅, 高さ）が画像の端に接しているかを返す。"""
    left, top, width, height = (int(value) for value in bounding_box)
    return left <= 0 or top <= 0 or left + width >= image_width or top + height >= image_height


def _draw_detection(
    image: np.ndarray, label: str, bounding_box: tuple[int, int, int, int]
) -> None:
    """検出結果の画像に、見つけた物体の枠と名前を描く。"""
    left, top, width, height = bounding_box
    cv2.rectangle(image, (left, top), (left + width, top + height), _BOX_COLOR, 2)
    cv2.putText(
        image, label, (left, max(top - 6, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, _BOX_COLOR, 2
    )


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = ColorDetector()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
