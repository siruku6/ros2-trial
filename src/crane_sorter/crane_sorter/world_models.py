"""Gazebo に置く物体と箱のモデル（SDF の文字列）を作る。

物体の衝突形状・質量・慣性・摩擦の値は、rt-net/crane_plus（jazzy ブランチ、34c4584）の
crane_plus_gazebo/models/aruco_cube_0/model.sdf（Apache License 2.0）から写した。
既存例で掴めることが分かっているキューブと同じ物理特性にし、見た目の色だけを変えるためである。
"""

_CUBE_TEMPLATE = """<?xml version="1.0" ?>
<sdf version="1.9">
  <model name="{name}">
    <link name="link">
      <visual name="visual">
        <geometry>
          <box>
            <size>{size} {size} {size}</size>
          </box>
        </geometry>
        <material>
          <ambient>{red} {green} {blue} 1</ambient>
          <diffuse>{red} {green} {blue} 1</diffuse>
          <specular>0.1 0.1 0.1 1</specular>
        </material>
      </visual>
      <collision name="collision">
        <geometry>
          <box>
            <size>{size} {size} {size}</size>
          </box>
        </geometry>
        <surface>
          <friction>
            <ode>
              <mu>0.9</mu>
              <mu2>0.9</mu2>
            </ode>
          </friction>
        </surface>
      </collision>
      <inertial>
        <inertia>
          <ixx>0.00002</ixx>
          <iyy>0.00002</iyy>
          <izz>0.00002</izz>
        </inertia>
        <mass>0.05</mass>
      </inertial>
    </link>
  </model>
</sdf>
"""

_BOX_PART_TEMPLATE = """
      <visual name="{part}_visual">
        <pose>{x} {y} {z} 0 0 0</pose>
        <geometry>
          <box>
            <size>{size_x} {size_y} {size_z}</size>
          </box>
        </geometry>
        <material>
          <ambient>{red} {green} {blue} 1</ambient>
          <diffuse>{red} {green} {blue} 1</diffuse>
        </material>
      </visual>
      <collision name="{part}_collision">
        <pose>{x} {y} {z} 0 0 0</pose>
        <geometry>
          <box>
            <size>{size_x} {size_y} {size_z}</size>
          </box>
        </geometry>
      </collision>"""

_BOX_TEMPLATE = """<?xml version="1.0" ?>
<sdf version="1.9">
  <model name="{name}">
    <static>true</static>
    <link name="link">{parts}
    </link>
  </model>
</sdf>
"""


def cube_sdf(name: str, size: float, color: list[float]) -> str:
    """色を指定したキューブのモデルを作る。

    Parameters
    ----------
    name : str
        Gazebo の中でのモデルの名前。
    size : float
        1 辺の長さ [m]。
    color : list[float]
        見た目の色（赤・緑・青、各 0〜1）。

    Returns
    -------
    str
        SDF の文字列。ros_gz_sim の create に渡して置く。
    """
    red, green, blue = color
    return _CUBE_TEMPLATE.format(name=name, size=size, red=red, green=green, blue=blue)


def box_sdf(
    name: str, outer_size: float, wall_height: float, wall_thickness: float, color: list[float]
) -> str:
    """物体を入れる、上の開いた四角い箱のモデルを作る。

    底板 1 枚と壁 4 枚からなる、動かない箱にする。モデルの原点は底面の中心。

    Parameters
    ----------
    name : str
        Gazebo の中でのモデルの名前。
    outer_size : float
        外側の 1 辺の長さ [m]。
    wall_height : float
        壁の高さ [m]。
    wall_thickness : float
        底板と壁の厚さ [m]。
    color : list[float]
        見た目の色（赤・緑・青、各 0〜1）。物体の色と区別できる色にする。

    Returns
    -------
    str
        SDF の文字列。
    """
    half = outer_size / 2 - wall_thickness / 2
    inner = outer_size - 2 * wall_thickness
    parts = [
        ('floor', 0.0, 0.0, wall_thickness / 2, outer_size, outer_size, wall_thickness),
        ('wall_front', half, 0.0, wall_height / 2, wall_thickness, outer_size, wall_height),
        ('wall_back', -half, 0.0, wall_height / 2, wall_thickness, outer_size, wall_height),
        ('wall_left', 0.0, half, wall_height / 2, inner, wall_thickness, wall_height),
        ('wall_right', 0.0, -half, wall_height / 2, inner, wall_thickness, wall_height),
    ]
    red, green, blue = color
    part_sdf = ''.join(
        _BOX_PART_TEMPLATE.format(
            part=part, x=x, y=y, z=z, size_x=size_x, size_y=size_y, size_z=size_z,
            red=red, green=green, blue=blue,
        )
        for part, x, y, z, size_x, size_y, size_z in parts
    )
    return _BOX_TEMPLATE.format(name=name, parts=part_sdf)
