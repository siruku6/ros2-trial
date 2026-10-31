"""仕分けの環境（机・腕・カメラ・箱・物体）を Gazebo に用意する launch。

腕・机・カメラは crane_plus_gazebo の launch をそのまま使い、机だけのワールドを渡す。
箱と物体はあとから置く。
物体の位置は起動のたびに変わる。seed を指定すると同じ配置を再現できる。

    ros2 launch crane_sorter sim.launch.py seed:=3
"""

import os
import random

from ament_index_python.packages import get_package_share_directory
from crane_sorter.layout import (
    box_placements, load_layout, sample_distractor_placements, sample_object_placements
)
from crane_sorter.world_models import box_sdf, object_sdf
from launch import LaunchContext, LaunchDescription
from launch.action import Action
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, LogInfo, OpaqueFunction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

# crane_plus_with_table.launch.py が腕を置く高さ（-z 1.015）。机の天板の高さに当たる [m]
_TABLE_TOP_HEIGHT = 1.015
# 置くときに机にめり込まないよう、少し浮かせる高さ [m]
_SPAWN_CLEARANCE = 0.001


def generate_launch_description() -> LaunchDescription:
    declare_seed = DeclareLaunchArgument(
        'seed',
        default_value='',
        description='物体の配置を決める乱数の種。空なら起動のたびに変わる',
    )
    declare_layout = DeclareLaunchArgument(
        'layout',
        default_value=os.path.join(
            get_package_share_directory('crane_sorter'), 'config', 'layout.yaml'
        ),
        description='箱と物体の配置の設定ファイル',
    )
    crane_plus_world = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            [
                get_package_share_directory('crane_plus_gazebo'),
                '/launch/crane_plus_with_table.launch.py',
            ]
        ),
        launch_arguments={
            'use_camera': 'true',
            'world_name': os.path.join(
                get_package_share_directory('crane_sorter'), 'worlds', 'sorting_table.sdf'
            ),
        }.items(),
    )
    return LaunchDescription(
        [
            declare_seed,
            declare_layout,
            crane_plus_world,
            OpaqueFunction(function=_place_boxes_and_objects),
        ]
    )


def _place_boxes_and_objects(context: LaunchContext) -> list[Action]:
    """設定を読んで物体の位置を決め、箱と物体を置く処理と、箱の tf を配信するノードを並べる。

    Reads:
      - layout 引数のファイル（既定は config/layout.yaml）
    """
    layout = load_layout(LaunchConfiguration('layout').perform(context))
    seed_text = LaunchConfiguration('seed').perform(context)
    seed = int(seed_text) if seed_text else random.randrange(1_000_000)
    actions: list[Action] = [LogInfo(msg=f'物体の配置の乱数の種: {seed}')]

    shape = layout['box_shape']
    for box in box_placements(layout):
        sdf = box_sdf(
            f'box_{box.name}',
            shape['outer_size'],
            shape['wall_height'],
            shape['wall_thickness'],
            shape['color'],
        )
        actions.append(_spawn_model(f'box_{box.name}', sdf, box.x, box.y, 0.0, 0.0))
        actions.append(_box_frame_publisher(box.name, box.x, box.y))

    rng = random.Random(seed)
    objects = sample_object_placements(layout, rng)
    distractors = sample_distractor_placements(layout, objects, rng)
    size = float(layout['object_size'])
    # 位置は設定と同じ順に並んでいる。掴む対象ではない物体は、モデルの名前で区別する
    for prefix, specs, placements in (
        ('object', layout['objects'], objects),
        ('distractor', layout.get('distractors', []), distractors),
    ):
        for spec, placement in zip(specs, placements):
            name = f'{prefix}_{placement.label}'
            sdf = object_sdf(name, spec.get('shape', 'cube'), size, spec['color'])
            actions.append(
                _spawn_model(name, sdf, placement.x, placement.y, size / 2, placement.yaw)
            )
            actions.append(
                LogInfo(msg=f'{name}: x={placement.x:.3f} y={placement.y:.3f} (crane_plus_base)')
            )
    return actions


def _spawn_model(name: str, sdf: str, x: float, y: float, z: float, yaw: float) -> Node:
    """crane_plus_base から見た位置に、SDF のモデルを Gazebo に置くノードを作る。"""
    return Node(
        package='ros_gz_sim',
        executable='create',
        output='screen',
        arguments=[
            '-name', name,
            '-string', sdf,
            '-x', str(x),
            '-y', str(y),
            '-z', str(_TABLE_TOP_HEIGHT + z + _SPAWN_CLEARANCE),
            '-Y', str(yaw),
        ],
    )


def _box_frame_publisher(name: str, x: float, y: float) -> Node:
    """箱の底面の中心を、tf の座標系 box_<名前> として配信し続けるノードを作る。"""
    return Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        output='screen',
        arguments=[
            '--x', str(x),
            '--y', str(y),
            '--z', '0.0',
            '--frame-id', 'crane_plus_base',
            '--child-frame-id', f'box_{name}',
        ],
    )
