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
# crane_plus_examples_py/launch/camera_example.launch.py を元にしたファイル。
# 変更点:
#   - 起動するノードを、自作の color_detector と pick_and_place_server に替えた
#   - use_sim_time の既定を true にした（Gazebo で使うため）。ただし自作の Python のノードは、
#     Gazebo の時計を使わない（理由は pick_and_place_server.py と下の color_detector を参照）
#   - 物体を探すノードの設定ファイルを渡すようにした

"""物体を探すノードと、掴んで箱に置く action のサーバを起動する launch。

sim.launch.py で Gazebo を起動し、腕の制御の準備が終わってから起動する。
"""

import os

from ament_index_python.packages import get_package_share_directory
from crane_plus_description.robot_description_loader import RobotDescriptionLoader
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from moveit_configs_utils import MoveItConfigsBuilder


def generate_launch_description() -> LaunchDescription:
    description_loader = RobotDescriptionLoader()
    declare_loaded_description = DeclareLaunchArgument(
        'loaded_description',
        default_value=description_loader.load(),
        description='Set robot_description text.  \
                    It is recommended to use RobotDescriptionLoader() \
                    in crane_plus_description.',
    )
    declare_use_sim_time = DeclareLaunchArgument(
        'use_sim_time',
        default_value='true',
        choices=['true', 'false'],
        description='Gazebo の時計を使うなら true',
    )

    moveit_config = (
        MoveItConfigsBuilder('crane_plus')
        .planning_scene_monitor(
            publish_robot_description=True,
            publish_robot_description_semantic=True,
        )
        .moveit_cpp(
            file_path=get_package_share_directory('crane_plus_examples_py')
            + '/config/crane_plus_moveit_py_examples.yaml'
        )
        .to_moveit_configs()
    )
    moveit_config.robot_description = {
        'robot_description': LaunchConfiguration('loaded_description')
    }

    # 下記Issue対応のためここでパラメータを設定する
    # https://github.com/moveit/moveit2/issues/2940#issuecomment-2401302214
    config_dict = moveit_config.to_dict()
    config_dict.update({'use_sim_time': LaunchConfiguration('use_sim_time')})

    pick_and_place_server = Node(
        package='crane_sorter',
        executable='pick_and_place_server',
        output='screen',
        parameters=[config_dict],
    )

    # 物体を探すノードは、時刻をカメラ画像に付いたものしか使わないので、Gazebo の時計を使わない。
    # 使うと毎秒 1000 回届く /clock の処理に CPU を取られる
    color_detector = Node(
        package='crane_sorter',
        executable='color_detector',
        output='screen',
        parameters=[
            os.path.join(
                get_package_share_directory('crane_sorter'), 'config', 'color_detector.yaml'
            ),
        ],
    )

    return LaunchDescription([
        declare_loaded_description,
        declare_use_sim_time,
        pick_and_place_server,
        color_detector,
    ])
