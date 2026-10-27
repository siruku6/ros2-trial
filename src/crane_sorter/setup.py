from glob import glob
import os

from setuptools import find_packages, setup

package_name = 'crane_sorter'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml', 'LICENSE.crane_plus']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
        (os.path.join('share', package_name, 'worlds'), glob('worlds/*.sdf')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='siruku6',
    maintainer_email='12032476+siruku6@users.noreply.github.com',
    description='Sort objects on a table into boxes with CRANE+ V2 in Gazebo',
    license='MIT AND Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'color_detector = crane_sorter.color_detector:main',
            'object_detector = crane_sorter.object_detector:main',
            'pick_and_place_server = crane_sorter.pick_and_place_server:main',
            'send_instruction = crane_sorter.send_instruction:main',
        ],
    },
)
