"""カメラ画像から、文で指定した物体を検出モデルで探し、見つけた位置を tf で配信するノード。

AI 版の物体を探すノードである。色の範囲で探す color_detector と同じ名前の tf の座標系
（object_<物体の名前>）を配信するので、掴む・置くを実行するノードは書き換えずに済む。

物体の名前は、検出モデルの点数と、前の画像までにその名前の物体を見た位置から決める
（label_tracker.py）。

検出モデルの処理を減らすため、推論は 1 秒あたり max_rate 回までにし、腕が動いている間は
推論しない。物体は腕が動かさない限り動かず、腕が動いている間は腕に隠れることも多いので、
その間に探しても得るものが少ない（issue #6）。

検出モデルのライブラリは NumPy 2 を要するので、このノードは仮想環境の Python で動かす
（sorter.launch.py の detector:=ai）。そのため、NumPy 1 向けにビルドされた cv_bridge と
image_geometry は使わず、画像の変換と位置の計算を NumPy だけで行う。
"""

import math
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
        half_precision: bool = self.declare_parameter('half_precision', True).value
        # 1 秒あたりの推論の回数の上限。0 なら、画像が届くたびに推論する
        self._max_rate: float = self.declare_parameter('max_rate', 2.0).value
        # 腕が動いている間は推論しない。腕の動きは、arm_frame の座標系の位置の変化で見る
        self._skip_while_arm_moves: bool = self.declare_parameter(
            'skip_while_arm_moves', True
        ).value
        self._arm_frame: str = self.declare_parameter('arm_frame', 'crane_plus_link_hand').value
        # 画像 1 枚ごとの arm_frame の位置の変化 [m] がこれより大きければ、腕が動いているとみなす
        arm_motion_threshold: float = self.declare_parameter('arm_motion_threshold', 0.002).value
        # 腕が止まってから、この時間 [s] たってから推論を再開する
        arm_still_seconds: float = self.declare_parameter('arm_still_seconds', 0.5).value
        self._plane_height: float = self.declare_parameter('plane_height', 0.035).value
        self._reference_frame: str = self.declare_parameter(
            'reference_frame', 'crane_plus_base'
        ).value
        self._frame_prefix: str = self.declare_parameter('frame_prefix', 'object_').value

        start = time.perf_counter()
        self._detector = create_detector(
            model, dict(zip(labels, prompts)), score_threshold, device, half_precision
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

        # 推論するかの判断に使う、直前の推論の時刻と腕の動き（時刻は画像の時刻 [s]）
        self._last_inference_at = -math.inf
        self._arm_motion = ArmMotionMonitor(arm_motion_threshold, arm_still_seconds)
        # 処理の回数を数え、一定の間隔でログに出す
        self._counts = {'images': 0, 'inferences': 0, 'arm_moving': 0, 'rate_limit': 0}
        self._inference_seconds = 0.0
        self.create_timer(
            self.declare_parameter('stats_period', 30.0).value, self._log_stats
        )

    def _on_camera(self, image_message: Image, info_message: CameraInfo) -> None:
        """画像 1 枚ごとに物体を探し、tf と検出結果の画像を配信する。"""
        try:
            camera_pose = self._tf_buffer.lookup_transform(
                self._reference_frame, image_message.header.frame_id, rclpy.time.Time()
            )
        except TransformException as error:
            self.get_logger().warn(f'カメラの位置を読めません: {error}', throttle_duration_sec=5.0)
            return

        stamp = image_message.header.stamp
        now = stamp.sec + stamp.nanosec * 1e-9
        self._counts['images'] += 1
        if self._skip_while_arm_moves and self._arm_is_moving(now):
            self._counts['arm_moving'] += 1
            return
        if self._max_rate > 0 and now - self._last_inference_at < 1.0 / self._max_rate:
            self._counts['rate_limit'] += 1
            return
        self._last_inference_at = now
        self._counts['inferences'] += 1
        started = time.perf_counter()

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
        assignment = self._tracker.update(
            [
                Observation(candidate, float(point[0]), float(point[1]))
                for (candidate, _), point in zip(located, in_reference)
            ],
            now,
        )
        for label, index in assignment.items():
            self._broadcast_object(label, located[index][1], image_message)

        detections = to_detections(
            [candidate for candidate, _ in located], self._detector.labels, assignment
        )
        self._detection_image_publisher.publish(
            _to_image_message(_draw_detections(rgb_image, detections), image_message)
        )
        self._inference_seconds += time.perf_counter() - started

    def _arm_is_moving(self, now: float) -> bool:
        """腕が動いているか、止まってから arm_still_seconds 秒たっていなければ True を返す。"""
        try:
            arm_pose = self._tf_buffer.lookup_transform(
                self._reference_frame, self._arm_frame, rclpy.time.Time()
            )
        except TransformException:
            # 腕の位置が分からなければ、動いているか判断できないので、推論は止めない
            return False
        translation = arm_pose.transform.translation
        return self._arm_motion.is_moving(
            np.array([translation.x, translation.y, translation.z]), now
        )

    def _log_stats(self) -> None:
        """前回のログから届いた画像の数と、推論した回数・時間をログに出す。"""
        counts = self._counts
        if counts['images'] == 0:
            return
        average_ms = 1000 * self._inference_seconds / max(counts['inferences'], 1)
        self.get_logger().info(
            f"画像 {counts['images']} 枚のうち推論 {counts['inferences']} 回"
            f"（1 回平均 {average_ms:.0f} ms）。腕が動いていて飛ばした {counts['arm_moving']} 枚、"
            f"回数の上限で飛ばした {counts['rate_limit']} 枚"
        )
        self._counts = dict.fromkeys(counts, 0)
        self._inference_seconds = 0.0

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


class ArmMotionMonitor:
    """腕の一部の位置の変化から、腕が動いているかを判断する。

    Parameters
    ----------
    threshold : float
        前回からの位置の変化 [m] がこれより大きければ、腕が動いたとみなす。
    still_seconds : float
        最後に動いてからこの時間 [s] がたつまでは、まだ動いているとみなす。
    """

    def __init__(self, threshold: float, still_seconds: float) -> None:
        self._threshold = threshold
        self._still_seconds = still_seconds
        self._last_position: np.ndarray | None = None
        self._moved_at = -math.inf

    def is_moving(self, position: np.ndarray, now: float) -> bool:
        """今の位置と時刻 [s] を受け取り、腕が動いているか、止まって間もなければ True を返す。"""
        if (
            self._last_position is not None
            and np.linalg.norm(position - self._last_position) > self._threshold
        ):
            self._moved_at = now
        self._last_position = position
        return now - self._moved_at < self._still_seconds


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
