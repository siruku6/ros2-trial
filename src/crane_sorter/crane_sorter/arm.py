# Copyright 2025 RT Corporation
# Copyright 2026 siruku6
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
# crane_plus_examples_py/crane_plus_examples_py/pick_and_place_tf.py を元にしたファイル。
# 変更点:
#   - 腕とグリッパを動かす部分だけを取り出し、ノードから切り離したクラスにした
#   - 掴む・置くの手順と tf の読み取りは、自作の pick_and_place_server.py に移した
#   - 置く位置と搬送の中間姿勢の定数を、左右どちらの箱にも使える形に変えた
#   - グリッパの今の角度を読む関数を足した

import math

from crane_plus_examples_py.utils import plan_and_execute
from geometry_msgs.msg import PoseStamped
from moveit.core.robot_state import RobotState
from moveit.planning import MoveItPy, PlanRequestParameters
from moveit_msgs.msg import (
    BoundingVolume,
    Constraints,
    JointConstraint,
    OrientationConstraint,
    PositionConstraint,
)
from rclpy.impl.rcutils_logger import RcutilsLogger
from scipy.spatial.transform import Rotation
from shape_msgs.msg import SolidPrimitive

# グリッパの開閉角度 [rad]
GRIPPER_DEFAULT = 0.0
GRIPPER_OPEN = math.radians(-30.0)
GRIPPER_CLOSE = math.radians(10.0)

# 待機姿勢の手先の位置姿勢（x, y, z [m], roll, pitch, yaw [deg]）
STANDBY = (0.0, 0.0, 0.3, 0.0, 0.0, 0.0)

# 掴むときの手先の高さ [m] とピッチ角 [deg]。ピッチ 90 度で手先が真下を向く
GRASP_HEIGHT = 0.04
GRASP_PITCH = 90.0

# 運ぶときの中間姿勢の手先の高さ [m] と、根元からの水平距離 [m]
TRANSIT_HEIGHT = 0.17
TRANSIT_RADIUS = 0.12

# 箱の上で物体を離すときの手先の高さ [m]
RELEASE_HEIGHT = 0.10

# 目標の位置姿勢の許容誤差。小さくして逆運動学の精度を上げる
_POSITION_TOLERANCE = 0.00001
_ORIENTATION_TOLERANCE = 0.0001


class CranePlusArm:
    """CRANE+ V2 の腕とグリッパを、手先の位置姿勢とグリッパの角度で動かす窓口。

    MoveItPy で動作を計画して実行する。掴む・置くの手順は持たず、呼び出し側が組み立てる。
    """

    def __init__(self, logger: RcutilsLogger) -> None:
        self._logger = logger
        # MoveItPy は内部に専用のノードを作り、ロボットの状態を受け取り続ける
        self._robot = MoveItPy(node_name='moveit_py')
        self._arm = self._robot.get_planning_component('arm_tcp')
        self._gripper = self._robot.get_planning_component('gripper')
        self._robot_model = self._robot.get_robot_model()
        self._arm_plan_parameters = self._make_plan_parameters()
        self._gripper_plan_parameters = self._make_plan_parameters()

    def move_to_home(self) -> bool:
        """SRDF に定義された home 姿勢に移る。起動直後の姿勢をそろえるのに使う。"""
        self._arm.set_start_state_to_current_state()
        self._arm.set_goal_state(configuration_name='home')
        return self._plan_and_execute(self._arm, self._arm_plan_parameters)

    def move_tool(
        self, x: float, y: float, z: float, roll: float, pitch: float, yaw: float
    ) -> bool:
        """手先を crane_plus_base から見た目標の位置姿勢に動かす。

        Parameters
        ----------
        x, y, z : float
            手先の目標位置 [m]。
        roll, pitch, yaw : float
            手先の目標姿勢 [deg]。

        Returns
        -------
        bool
            計画と実行がどちらも成功したら True。
        """
        target_pose = PoseStamped()
        target_pose.header.frame_id = 'crane_plus_base'
        target_pose.pose.position.x = x
        target_pose.pose.position.y = y
        target_pose.pose.position.z = z
        quaternion = Rotation.from_euler('xyz', [roll, pitch, yaw], degrees=True).as_quat()
        target_pose.pose.orientation.x = quaternion[0]
        target_pose.pose.orientation.y = quaternion[1]
        target_pose.pose.orientation.z = quaternion[2]
        target_pose.pose.orientation.w = quaternion[3]

        goal_constraints = Constraints()
        goal_constraints.name = 'tolerance_goal'
        goal_constraints.position_constraints.append(self._make_position_constraint(target_pose))
        goal_constraints.orientation_constraints.append(
            self._make_orientation_constraint(target_pose)
        )

        self._arm.set_start_state_to_current_state()
        self._arm.set_goal_state(motion_plan_constraints=[goal_constraints])
        return self._plan_and_execute(self._arm, self._arm_plan_parameters)

    def move_gripper(self, angle: float) -> bool:
        """グリッパを指定の角度 [rad] まで開閉する。物体に当たって止まると失敗が返ることがある。"""
        self._gripper.set_start_state_to_current_state()
        robot_state = RobotState(self._robot_model)
        robot_state.set_joint_group_positions('gripper', [angle])
        self._gripper.set_goal_state(robot_state=robot_state)
        return self._plan_and_execute(self._gripper, self._gripper_plan_parameters)

    def gripper_angle(self) -> float:
        """グリッパの今の角度 [rad] を返す。物体を掴めたかの判定に使う。"""
        with self._robot.get_planning_scene_monitor().read_only() as scene:
            return float(scene.current_state.get_joint_group_positions('gripper')[0])

    def set_joint_constraints(self) -> None:
        """腕が無理な姿勢を通らないよう、一部の関節の可動範囲を動作計画に課す。"""
        constraints = Constraints()
        constraints.name = 'arm_constraints'

        joint_constraint = JointConstraint()
        joint_constraint.joint_name = 'crane_plus_joint1'
        joint_constraint.position = 0.0
        joint_constraint.tolerance_above = math.radians(100)
        joint_constraint.tolerance_below = math.radians(100)
        joint_constraint.weight = 1.0
        constraints.joint_constraints.append(joint_constraint)

        joint_constraint = JointConstraint()
        joint_constraint.joint_name = 'crane_plus_joint3'
        joint_constraint.position = 0.0
        joint_constraint.tolerance_above = math.radians(0)
        joint_constraint.tolerance_below = math.radians(180)
        joint_constraint.weight = 1.0
        constraints.joint_constraints.append(joint_constraint)

        self._arm.set_path_constraints(constraints)

    def clear_joint_constraints(self) -> None:
        """set_joint_constraints で課した可動範囲を外す。"""
        self._arm.clear_path_constraints()

    def _make_plan_parameters(self) -> PlanRequestParameters:
        """設定ファイルの ompl_rrtc の項目で、速度と加速度を最大にした計画の条件を作る。"""
        parameters = PlanRequestParameters(self._robot, 'ompl_rrtc')
        parameters.max_velocity_scaling_factor = 1.0
        parameters.max_acceleration_scaling_factor = 1.0
        return parameters

    def _make_position_constraint(self, target_pose: PoseStamped) -> PositionConstraint:
        """手先の位置を目標の周りの小さな球の中に収める条件を作る。"""
        primitive = SolidPrimitive()
        primitive.type = SolidPrimitive.SPHERE
        primitive.dimensions = [_POSITION_TOLERANCE]
        tolerance_region = BoundingVolume()
        tolerance_region.primitives.append(primitive)
        tolerance_region.primitive_poses.append(target_pose.pose)

        position_constraint = PositionConstraint()
        position_constraint.header.frame_id = target_pose.header.frame_id
        position_constraint.link_name = 'crane_plus_link_tcp'
        position_constraint.constraint_region = tolerance_region
        position_constraint.weight = 1.0
        return position_constraint

    def _make_orientation_constraint(self, target_pose: PoseStamped) -> OrientationConstraint:
        """手先の姿勢を目標にほぼ一致させる条件を作る。"""
        orientation_constraint = OrientationConstraint()
        orientation_constraint.header.frame_id = target_pose.header.frame_id
        orientation_constraint.link_name = 'crane_plus_link_tcp'
        orientation_constraint.orientation = target_pose.pose.orientation
        orientation_constraint.absolute_x_axis_tolerance = _ORIENTATION_TOLERANCE
        orientation_constraint.absolute_y_axis_tolerance = _ORIENTATION_TOLERANCE
        orientation_constraint.absolute_z_axis_tolerance = _ORIENTATION_TOLERANCE
        orientation_constraint.weight = 1.0
        return orientation_constraint

    def _plan_and_execute(self, component, plan_parameters: PlanRequestParameters) -> bool:
        """動作を計画して実行し、成否を bool で返す。"""
        # 計画に失敗すると False が、実行すると ExecutionStatus が返る。後者は成功なら真になる
        return bool(
            plan_and_execute(
                self._robot, component, self._logger, single_plan_parameters=plan_parameters
            )
        )
