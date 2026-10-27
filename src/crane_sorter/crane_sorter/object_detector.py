"""カメラ画像から、文で指定した物体を検出モデルで探し、見つけた位置を tf で配信するノード。

AI 版の物体を探すノードである。色の範囲で探す color_detector と同じ名前の tf の座標系
（object_<物体の名前>）を配信するので、掴む・置くを実行するノードは書き換えずに済む。

物体の名前は、検出モデルの点数と、前の画像までにその名前の物体を見た位置から決める
（label_tracker.py）。

検出モデルのライブラリは NumPy 2 を要するので、このノードは仮想環境の Python で動かす
（sorter.launch.py の detector:=ai）。そのため、NumPy 1 向けにビルドされた cv_bridge と
image_geometry は使わず、画像の変換と位置の計算を NumPy だけで行う。
"""

import time

from crane_sorter.camera_geometry import locate_on_plane, pixel_to_ray, rotate
from crane_sorter.detectors import create_detector, Detection, to_detections
from crane_sorter.label_tracker import LabelTracker, Observation
import cv2
from geometry_msgs.msg import TransformStamped
import message_filters
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile
from sensor_msgs.msg import CameraInfo, Image
from tf2_ros import Buffer, TransformBroadcaster, TransformException, TransformListener

# 枠を描く色（RGB）
_BOX_COLOR = (255, 255, 0)
# 推論が画像の届く間隔より遅いときに、古い画像を溜めないよう、受け取る数を絞る
_CAMERA_QOS = QoSProfile(depth=2)


class ObjectDetector(Node):
    """カメラ画像から検出モデルで物体を探し、見つけた位置を tf の座標系として配信するノード。"""

    def __init__(self) -> None:
        super().__init__('object_detector')
        labels: list[str] = list(self.declare_parameter('labels', ['red_cube']).value)
        prompts: list[str] = list(self.declare_parameter('prompts', ['red cube']).value)
        if len(labels) != len(prompts):
            raise ValueError('labels と prompts の要素数をそろえてください')
        model: str = self.declare_parameter('model', 'owlv2').value
        score_threshold: float = self.declare_parameter('score_threshold', 0.1).value
        device: str = self.declare_parameter('device', 'cuda').value
        self._plane_height: float = self.declare_parameter('plane_height', 0.035).value
        self._reference_frame: str = self.declare_parameter(
            'reference_frame', 'crane_plus_base'
        ).value
        self._frame_prefix: str = self.declare_parameter('frame_prefix', 'object_').value

        start = time.perf_counter()
        self._detector = create_detector(
            model, dict(zip(labels, prompts)), score_threshold, device
        )
        self._tracker = LabelTracker(labels, score_threshold)
        self.get_logger().info(
            f'検出モデル {model} を {time.perf_counter() - start:.1f} 秒で用意しました。'
            f'探す物体: {dict(zip(labels, prompts))}'
        )

        # 画像とカメラの情報は同じ時刻のものを組にして受け取る
        image_subscriber = message_filters.Subscriber(
            self, Image, 'image_raw', qos_profile=_CAMERA_QOS
        )
        info_subscriber = message_filters.Subscriber(
            self, CameraInfo, 'camera_info', qos_profile=_CAMERA_QOS
        )
        self._synchronizer = message_filters.TimeSynchronizer(
            [image_subscriber, info_subscriber], 2
        )
        self._synchronizer.registerCallback(self._on_camera)

        self._detection_image_publisher = self.create_publisher(Image, '~/detections', 10)
        self._tf_broadcaster = TransformBroadcaster(self)
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)

    def _on_camera(self, image_message: Image, info_message: CameraInfo) -> None:
        """画像 1 枚ごとに物体を探し、tf と検出結果の画像を配信する。"""
        try:
            camera_pose = self._tf_buffer.lookup_transform(
                self._reference_frame, image_message.header.frame_id, rclpy.time.Time()
            )
        except TransformException as error:
            self.get_logger().warn(f'カメラの位置を読めません: {error}', throttle_duration_sec=5.0)
            return

        rgb_image = _to_rgb_array(image_message)
        candidates = self._detector.candidates(rgb_image)
        rotation = camera_pose.transform.rotation
        translation = camera_pose.transform.translation
        camera_rotation = np.array([rotation.x, rotation.y, rotation.z, rotation.w])
        camera_translation = np.array([translation.x, translation.y, translation.z])
        camera_matrix = np.array(info_message.k, dtype=float).reshape(3, 3)

        # 候補ごとに位置を求める。名前は、前の画像までに見た位置と合わせて付ける
        located = []
        for candidate in candidates:
            center = ((candidate.box[0] + candidate.box[2]) / 2,
                      (candidate.box[1] + candidate.box[3]) / 2)
            position = locate_on_plane(
                pixel_to_ray(camera_matrix, center),
                camera_rotation, camera_translation, self._plane_height,
            )
            if position is not None:
                located.append((candidate, position))
        in_reference = [rotate(camera_rotation, position) + camera_translation
                        for _, position in located]
        stamp = image_message.header.stamp
        assignment = self._tracker.update(
            [
                Observation(candidate, float(point[0]), float(point[1]))
                for (candidate, _), point in zip(located, in_reference)
            ],
            stamp.sec + stamp.nanosec * 1e-9,
        )
        for label, index in assignment.items():
            self._broadcast_object(label, located[index][1], image_message)

        detections = to_detections(
            [candidate for candidate, _ in located], self._detector.labels, assignment
        )
        self._detection_image_publisher.publish(
            _to_image_message(_draw_detections(rgb_image, detections), image_message)
        )

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


def _to_rgb_array(image_message: Image) -> np.ndarray:
    """sensor_msgs/Image（rgb8 か bgr8）を、RGB の配列にする。"""
    rows = np.frombuffer(image_message.data, dtype=np.uint8).reshape(
        image_message.height, image_message.step
    )
    # 1 行の末尾に詰め物があるときに備え、幅の分だけ使う
    channels = rows[:, :image_message.width * 3].reshape(
        image_message.height, image_message.width, 3
    )
    if image_message.encoding == 'bgr8':
        channels = channels[:, :, ::-1]
    return np.ascontiguousarray(channels)


def _to_image_message(rgb_image: np.ndarray, source: Image) -> Image:
    """RGB の配列を、元の画像と同じ時刻と座標系の sensor_msgs/Image にする。"""
    message = Image()
    message.header = source.header
    message.height, message.width = rgb_image.shape[:2]
    message.encoding = 'rgb8'
    message.step = message.width * 3
    message.data = rgb_image.tobytes()
    return message


def _draw_detections(rgb_image: np.ndarray, detections: list[Detection]) -> np.ndarray:
    """見つけた物体の枠と、名前と点数を描いた画像を返す。"""
    image = rgb_image.copy()
    for detection in detections:
        left, top, right, bottom = (int(value) for value in detection.box)
        cv2.rectangle(image, (left, top), (right, bottom), _BOX_COLOR, 2)
        cv2.putText(
            image, f'{detection.label} {detection.score:.2f}', (left, max(top - 6, 12)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, _BOX_COLOR, 2,
        )
    return image


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = ObjectDetector()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
