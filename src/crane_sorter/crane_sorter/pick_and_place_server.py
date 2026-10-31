"""物体を 1 個掴んで箱に置く依頼を、ROS 2 の action として受け付けるノード。"""

import math
import threading
import time

from crane_sorter import arm
from crane_sorter.arm import CranePlusArm
from crane_sorter_interfaces.action import PickAndPlace
from geometry_msgs.msg import Point, TransformStamped
import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.action.server import ServerGoalHandle
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.duration import Duration
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.time import Time
from tf2_ros import Buffer, TransformException, TransformListener
import yaml

_REFERENCE_FRAME = 'crane_plus_base'
_OBJECT_FRAME_PREFIX = 'object_'
_BOX_FRAME_PREFIX = 'box_'
# 関節の状態が届くたびに更新される座標系。物体の位置の鮮度を測る基準に使う
_ROBOT_STATE_FRAME = 'crane_plus_link1'


class _StepFailed(Exception):
    """腕の動作や確認に失敗して、依頼を中止するときに投げる。"""


class _Canceled(Exception):
    """依頼の取り消しを受けて、途中でやめるときに投げる。"""


class PickAndPlaceServer(Node):
    """tf で物体と箱の位置を読み、物体を掴んで箱に置く手順を順に実行するノード。

    依頼は action で受ける。物体は object_<名前>、箱は box_<名前> という tf の座標系で表され、
    このノードは物体をどう見つけたかを知らない。同時に受ける依頼は 1 件だけである。
    """

    def __init__(self) -> None:
        # このノードは Gazebo の時計（/clock）を使わない。/clock は毎秒 1000 回届き、Python で
        # 受け取ると、待機中でも CPU を 1 コア分ほど使い、ほかのメッセージの処理も遅れたため。
        # 時刻は tf の中で比べる（_lookup_object）。MoveItPy の内部のノードは、launch の設定どおり
        # Gazebo の時計を使う
        super().__init__(
            'pick_and_place_server',
            parameter_overrides=[Parameter('use_sim_time', value=False)],
        )
        # 物体の位置が、腕の関節の状態よりこの秒数以上古ければ、見えていないとみなす
        self._max_object_age = Duration(
            seconds=self.declare_parameter('max_object_age', 1.0).value
        )
        # 物体の位置が古いときに、新しくなるのを待つ時間の上限 [s]。AI 版の物体を探すノードは、
        # 腕が動いている間は物体を探さないので、腕が止まった直後は位置が古いことがある
        self._object_wait_seconds: float = self.declare_parameter(
            'object_wait_seconds', 3.0
        ).value
        # 閉じたあとの角度が、閉じきった角度（10 度）からこの角度以内なら、掴み損ねとみなす。
        # 5 cm のキューブを掴むと約 6 度で止まり、何もないと 10 度まで閉じた（2026-10-25）
        self._empty_grasp_margin = math.radians(
            self.declare_parameter('empty_grasp_margin_deg', 2.0).value
        )
        # 箱の中心からこの水平距離 [m] 以内にある物体は、箱の中にあるとみなす
        self._in_box_radius: float = self.declare_parameter('in_box_radius', 0.07).value
        # tf は専用のノードで、起動直後から別のスレッドで受け取り続ける。このノードの executor は
        # 腕を初期の姿勢に動かし終えるまで回らないので、同じ executor で受けると、その間に tf が
        # 溜まって処理が遅れ、物体の位置が実際より古く見えたため
        self._tf_node = rclpy.create_node(
            'pick_and_place_server_tf',
            parameter_overrides=[Parameter('use_sim_time', value=False)],
        )
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self._tf_node, spin_thread=True)
        self._busy = False
        self._busy_lock = threading.Lock()
        # 実行中の依頼で、腕が待機の姿勢を離れたか。中止したあとに戻る必要があるかの判断に使う
        self._left_standby = False

        self._arm = CranePlusArm(self.get_logger())
        self._arm.move_to_home()
        self._arm.set_joint_constraints()
        self._arm.move_tool(*arm.STANDBY)

        # 依頼の実行中も取り消しや新しい依頼の受け付けを続けられるよう、
        # 複数のスレッドで並行して呼べるコールバックのグループに入れる
        self._action_server = ActionServer(
            self,
            PickAndPlace,
            'pick_and_place',
            execute_callback=self._execute,
            goal_callback=self._on_goal,
            cancel_callback=self._on_cancel,
            callback_group=ReentrantCallbackGroup(),
        )
        self.get_logger().info('依頼を待っています（action: /pick_and_place）')

    def shutdown(self) -> None:
        """終了の前に、動作計画に課した関節の制限を外し、tf の受信を止める。"""
        self._arm.clear_joint_constraints()
        self._tf_listener.unregister()
        self._tf_node.destroy_node()

    def _on_goal(self, goal_request: PickAndPlace.Goal) -> GoalResponse:
        """新しい依頼を受けるか決める。腕は 1 本なので、実行中は断る。"""
        with self._busy_lock:
            if self._busy:
                self.get_logger().warn(
                    f'実行中のため断りました: {goal_request.object_label} → {goal_request.box_name}'
                )
                return GoalResponse.REJECT
            self._busy = True
        return GoalResponse.ACCEPT

    def _on_cancel(self, goal_handle: ServerGoalHandle) -> CancelResponse:
        """取り消しの求めを受け付ける。実際にやめるのは、物体を掴む前の区切りに限る。"""
        self.get_logger().info('取り消しを求められました')
        return CancelResponse.ACCEPT

    def _execute(self, goal_handle: ServerGoalHandle) -> PickAndPlace.Result:
        """依頼を 1 件実行し、成功・失敗・取り消しのどれかで終える。"""
        request: PickAndPlace.Goal = goal_handle.request
        result = PickAndPlace.Result()
        self.get_logger().info(f'依頼: 物体 {request.object_label} を箱 {request.box_name} へ')
        self._left_standby = False
        try:
            message = self._pick_and_place(goal_handle, request, result)
        except _Canceled:
            self._return_to_standby()
            result.message = '取り消しました'
            goal_handle.canceled()
        except _StepFailed as failure:
            self._return_to_standby()
            result.message = str(failure)
            goal_handle.abort()
        else:
            result.success = True
            result.message = message
            goal_handle.succeed()
        finally:
            with self._busy_lock:
                self._busy = False
        self.get_logger().info(f'結果: {result.message}')
        return result

    def _pick_and_place(
        self,
        goal_handle: ServerGoalHandle,
        request: PickAndPlace.Goal,
        result: PickAndPlace.Result,
    ) -> str:
        """物体を探し、掴み、箱の上まで運んで離し、待機の姿勢に戻る。結果の説明を返す。"""
        self._publish_stage(goal_handle, 'looking')
        target = self._lookup_object(request.object_label)
        box = self._lookup_box(request.box_name)
        result.object_position = target

        # 箱の中の物体は、壁が邪魔で掴めないので取り出さない
        current_box = self._box_containing(target)
        if current_box == request.box_name:
            return f'物体 {request.object_label} はすでに箱 {current_box} の中にあります'
        if current_box is not None:
            raise _StepFailed(
                f'物体 {request.object_label} は箱 {current_box} の中にあり、取り出せません'
            )
        target_yaw = math.degrees(math.atan2(target.y, target.x))

        self._raise_if_canceled(goal_handle)
        self._publish_stage(goal_handle, 'approaching')
        self._left_standby = True
        self._arm.move_gripper(arm.GRIPPER_OPEN)
        self._require(
            self._arm.move_tool(0.0, 0.0, arm.STANDBY[2], 0.0, 0.0, target_yaw),
            '物体の方へ向きを変えられませんでした',
        )

        self._raise_if_canceled(goal_handle)
        self._publish_stage(goal_handle, 'grasping')
        self._require(
            self._arm.move_tool(
                target.x, target.y, arm.GRASP_HEIGHT, 0.0, arm.GRASP_PITCH, target_yaw
            ),
            f'物体の位置 ({target.x:.3f}, {target.y:.3f}) に手先を下ろせませんでした',
        )
        # 物体に当たって指が止まると、閉じる動作は失敗として返ることがあるので、結果は見ない
        self._arm.move_gripper(arm.GRIPPER_CLOSE)
        self._require_grasped()

        # 掴んだあとは、物体を途中で落とさないよう、取り消しを受けても箱まで運ぶ
        self._publish_stage(goal_handle, 'carrying')
        side = 1.0 if box.y >= 0.0 else -1.0
        self._require(
            self._arm.move_tool(
                arm.TRANSIT_RADIUS, 0.0, arm.TRANSIT_HEIGHT, 0.0, arm.GRASP_PITCH, 0.0
            ),
            '物体を持ち上げられませんでした',
        )
        self._require(
            self._arm.move_tool(
                0.0, side * arm.TRANSIT_RADIUS, arm.TRANSIT_HEIGHT,
                0.0, arm.GRASP_PITCH, side * 90.0,
            ),
            '箱の方へ運べませんでした',
        )
        box_yaw = math.degrees(math.atan2(box.y, box.x))
        self._require(
            self._arm.move_tool(box.x, box.y, arm.RELEASE_HEIGHT, 0.0, arm.GRASP_PITCH, box_yaw),
            '箱の上に手先を動かせませんでした',
        )

        self._publish_stage(goal_handle, 'releasing')
        self._arm.move_gripper(arm.GRIPPER_OPEN)

        self._publish_stage(goal_handle, 'returning')
        self._require(self._arm.move_tool(*arm.STANDBY), '待機の姿勢に戻れませんでした')
        self._arm.move_gripper(arm.GRIPPER_DEFAULT)
        return f'物体 {request.object_label} を箱 {request.box_name} に置きました'

    def _lookup_object(self, label: str) -> Point:
        """物体の位置を tf から読む。最近見えていない物体は、見つからない扱いにする。

        鮮度は、物体の座標系と腕の関節の座標系が最後に更新された時刻の差で測る。
        どちらにも Gazebo の時計の時刻が付いているので、このノードが Gazebo の時計を使わなくても比べられる。
        位置が古ければ、新しくなるまで object_wait_seconds 秒まで待つ。
        """
        frame = f'{_OBJECT_FRAME_PREFIX}{label}'
        deadline = time.monotonic() + self._object_wait_seconds
        while True:
            transform = self._lookup_transform(frame)
            robot_state = self._lookup_transform(_ROBOT_STATE_FRAME)
            age = None
            if transform is not None and robot_state is not None:
                age = (
                    Time.from_msg(robot_state.header.stamp) - Time.from_msg(transform.header.stamp)
                )
                if age <= self._max_object_age:
                    self.get_logger().info(
                        f'物体 {label} の位置は、腕の状態より {age.nanoseconds / 1e9:.2f} 秒前の検出'
                    )
                    return _position_of(transform)
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)

        if transform is None:
            raise _StepFailed(f'物体 {label} が見つかりません（tf の {frame} がありません）')
        if robot_state is None:
            raise _StepFailed(f'腕の状態を読めません（tf の {_ROBOT_STATE_FRAME} がありません）')
        raise _StepFailed(f'物体 {label} が {age.nanoseconds / 1e9:.1f} 秒前から見えていません')

    def _lookup_box(self, name: str) -> Point:
        """箱の位置を tf から読む。"""
        frame = f'{_BOX_FRAME_PREFIX}{name}'
        transform = self._lookup_transform(frame)
        if transform is None:
            raise _StepFailed(f'箱 {name} が見つかりません（tf の {frame} がありません）')
        return _position_of(transform)

    def _box_containing(self, target: Point) -> str | None:
        """物体が入っている箱の名前を返す。どの箱にも入っていなければ None を返す。"""
        known_frames: dict = yaml.safe_load(self._tf_buffer.all_frames_as_yaml()) or {}
        for frame in known_frames:
            if not frame.startswith(_BOX_FRAME_PREFIX):
                continue
            transform = self._lookup_transform(frame)
            if transform is None:
                continue
            box = _position_of(transform)
            if math.hypot(target.x - box.x, target.y - box.y) <= self._in_box_radius:
                return frame[len(_BOX_FRAME_PREFIX):]
        return None

    def _lookup_transform(self, frame: str) -> TransformStamped | None:
        """crane_plus_base から見た座標系の最新の位置姿勢を返す。なければ None を返す。"""
        try:
            return self._tf_buffer.lookup_transform(_REFERENCE_FRAME, frame, Time())
        except TransformException:
            return None

    def _require_grasped(self) -> None:
        """指が閉じきっていたら、物体を掴み損ねたとみなして中止する。"""
        angle = self._arm.gripper_angle()
        self.get_logger().info(f'閉じたあとのグリッパの角度: {math.degrees(angle):.1f} 度')
        if angle >= arm.GRIPPER_CLOSE - self._empty_grasp_margin:
            raise _StepFailed('物体を掴めませんでした（指が閉じきりました）')

    def _return_to_standby(self) -> None:
        """中止したあと、手に持っている物があれば離し、待機の姿勢に戻る。腕が動く前なら何もしない。"""
        if not self._left_standby:
            return
        self._arm.move_gripper(arm.GRIPPER_OPEN)
        self._arm.move_tool(*arm.STANDBY)
        self._arm.move_gripper(arm.GRIPPER_DEFAULT)

    def _publish_stage(self, goal_handle: ServerGoalHandle, stage: str) -> None:
        """いまの段階を、依頼主に途中経過として送る。"""
        self.get_logger().info(f'段階: {stage}')
        goal_handle.publish_feedback(PickAndPlace.Feedback(stage=stage))

    @staticmethod
    def _raise_if_canceled(goal_handle: ServerGoalHandle) -> None:
        """取り消しを求められていたら、ここでやめる。"""
        if goal_handle.is_cancel_requested:
            raise _Canceled()

    @staticmethod
    def _require(succeeded: bool, failure_message: str) -> None:
        """腕の動作が失敗していたら、理由を付けて中止する。"""
        if not succeeded:
            raise _StepFailed(failure_message)


def _position_of(transform: TransformStamped) -> Point:
    """座標系の原点の位置を取り出す。"""
    translation = transform.transform.translation
    return Point(x=translation.x, y=translation.y, z=translation.z)


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = PickAndPlaceServer()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
