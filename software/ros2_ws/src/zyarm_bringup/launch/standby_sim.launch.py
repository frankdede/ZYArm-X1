from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node


def generate_launch_description():
    bringup_share = Path(get_package_share_directory("zyarm_bringup"))

    mock_bringup = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            str(bringup_share / "launch" / "bringup_x1_standard_ros2_control.launch.py")
        ),
        launch_arguments={"use_rviz": "false"}.items(),
    )

    standby_manager = Node(
        package="zyarm_bringup",
        executable="standby_manager",
        name="zyarm_standby_manager",
        output="screen",
        parameters=[{"mode": "sim", "sim_motion_duration_sec": 4.0}],
    )

    foxglove_bridge = Node(
        package="foxglove_bridge",
        executable="foxglove_bridge",
        name="zyarm_standby_sim_foxglove_bridge",
        output="screen",
        parameters=[{"address": "0.0.0.0", "port": 8766}],
    )

    return LaunchDescription([mock_bringup, standby_manager, foxglove_bridge])
