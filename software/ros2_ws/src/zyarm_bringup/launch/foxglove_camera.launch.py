from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    arguments = [
        DeclareLaunchArgument(
            "device",
            default_value="/dev/v4l/by-id/usb-XHH-260128-A_2M-video-index0",
        ),
        DeclareLaunchArgument("topic", default_value="/camera/image/compressed"),
        DeclareLaunchArgument("frame_id", default_value="camera_link"),
        DeclareLaunchArgument("width", default_value="640"),
        DeclareLaunchArgument("height", default_value="480"),
        DeclareLaunchArgument("fps", default_value="30.0"),
        DeclareLaunchArgument("fourcc", default_value="MJPG"),
    ]

    camera = Node(
        package="zyarm_bringup",
        executable="camera_streamer",
        name="zyarm_camera_streamer",
        output="screen",
        parameters=[
            {
                "device": LaunchConfiguration("device"),
                "topic": LaunchConfiguration("topic"),
                "frame_id": LaunchConfiguration("frame_id"),
                "width": ParameterValue(LaunchConfiguration("width"), value_type=int),
                "height": ParameterValue(LaunchConfiguration("height"), value_type=int),
                "fps": ParameterValue(LaunchConfiguration("fps"), value_type=float),
                "fourcc": LaunchConfiguration("fourcc"),
            }
        ],
    )

    return LaunchDescription([*arguments, camera])
