# ZYArm-X1 Foxglove/ROS 2 联调记录

更新时间：2026-07-20

本文记录 `pi-4b` 上已经实际构建和验证的环境、ROS 2 控制、固件模式服务、Foxglove 3D 和 USB 摄像头链路。它是联调结果记录，不替代机械臂安全操作规范。

## 运行环境

| 项目 | 已验证配置 |
| --- | --- |
| 主机 | Raspberry Pi 4B，Ubuntu 24.04，aarch64 |
| ROS 2 | Jazzy |
| Python | 系统 Python 3.12.3 |
| 虚拟环境 | `/home/frank/venv` |
| ROS 工作区 | `/home/frank/ZYArm-X1/software/ros2_ws` |
| 机械臂串口 | `/dev/ttyUSB0` |
| Foxglove Bridge | `ws://192.168.68.107:8765` |

`/home/frank/.bashrc` 已配置为交互式 Bash 自动激活虚拟环境。`frank` 已加入 `dialout` 和 `video` 组，分别用于访问串口和摄像头。

## Foxglove 与 ROS 2 数据链

`foxglove-bridge.service` 监听 `0.0.0.0:8765`。机械臂状态、TF、模型、服务和摄像头视频共用同一个 WebSocket：

```text
ros2_control -> /joint_states -> robot_state_publisher -> /tf
URDF ---------------------------------------------> /robot_description
USB camera -> V4L2 native MJPEG -> /camera/image/compressed
ROS 2 topics/services -> foxglove_bridge -> ws://192.168.68.107:8765
```

Foxglove 3D 面板建议设置：

```text
Fixed frame: base_link
Display frame: base_link
Joint states topic: /joint_states
Display mode: Visual
```

摄像头画面使用 **Image** 面板并选择：

```text
/camera/image/compressed
```

## 仿真和真机控制

仿真使用 `mock_components/GenericSystem`，不会访问真实串口。固件模式集成测试使用隔离的 ROS Domain 42 和 Foxglove 端口 `8766`：

```bash
source /opt/ros/jazzy/setup.bash
source /home/frank/ZYArm-X1/software/ros2_ws/install/setup.bash
ROS_DOMAIN_ID=42 ros2 launch zyarm_bringup standby_sim.launch.py
```

真机入口：

```bash
source /opt/ros/jazzy/setup.bash
source /home/frank/ZYArm-X1/software/ros2_ws/install/setup.bash
ros2 launch zyarm_bringup \
  bringup_x1_standard_real_ros2_control.launch.py use_rviz:=false
```

已验证以下控制器在 sim 和 real 中为 `active`：

```text
arm_controller
gripper_controller
joint_state_broadcaster
```

真实硬件插件会从当前反馈位置初始化 command，激活时发送 no-change `CMD36`，避免软件启动主动回零。切换 sim/real 前仍必须确认机械臂已支撑、人员已离开且工作区无障碍物。

## Standby、Unload 和 Resume

对 Foxglove 和 ROS 客户端公开的服务均为 `std_srvs/srv/Trigger`：

| 服务 | 真机行为 | 最终控制状态 |
| --- | --- | --- |
| `/zyarm/reset` | 停控制器、暂停硬件循环、发送 `CMD1`、恢复原控制器 | active |
| `/zyarm/standby` | 停控制器、暂停硬件循环、发送 `CMD38`、读取当前位置并恢复 | active |
| `/zyarm/unload` | 停控制器、暂停硬件循环并发送 `CMD23` | inactive，关节卸力 |
| `/zyarm/resume` | 激活硬件、从当前位置同步 command 并恢复原控制器 | active |

在 Foxglove 的 Service Call 面板选择对应服务，请求体填写空 JSON：

```json
{}
```

点击 Call 前仍要按真机安全条件确认。尤其是 `/zyarm/unload`，必须先扶稳机械臂；`/zyarm/resume` 前必须放稳机械臂、手已离开且工作区无人。

硬件插件内部的 `/zyarm/reset_raw`、`/zyarm/standby_raw` 和 `/zyarm/unload_raw` 只负责在串口唯一所有者中发送固件命令并等待完成 ACK，外部操作应调用上表中的托管服务。`CMD1 reset` 可能触发固件动作，执行前应像其他运动命令一样确认机械臂姿态和工作区。

已验证的 unload/resume 状态变化：

```text
controllers active + hardware active
  -> /zyarm/unload
controllers inactive + hardware inactive
  -> /zyarm/resume
controllers active + hardware active
```

`CMD23` 不是外部电源断电。执行 unload 后机械臂会在重力作用下下坠，调用前必须可靠支撑机械臂。Unload 状态下 `/zyarm/standby` 会被拒绝，重复 unload 为幂等操作。

## USB 摄像头链路

已验证设备：

```text
Sunplus SPCA2281 Web Camera
/dev/v4l/by-id/usb-XHH-260128-A_2M-video-index0
```

启动命令：

```bash
source /opt/ros/jazzy/setup.bash
source /home/frank/ZYArm-X1/software/ros2_ws/install/setup.bash
ros2 launch zyarm_bringup foxglove_camera.launch.py
```

当前默认和实测结果：

| 项目 | 数值 |
| --- | --- |
| 输入格式 | 摄像头原生 MJPEG |
| 默认分辨率 | `640x480` |
| 实测帧率 | `29.99 fps` |
| ROS 消息 | `sensor_msgs/msg/CompressedImage` |
| Topic | `/camera/image/compressed` |
| 平均吞吐 | 约 `1.45 MiB/s`，随场景复杂度变化 |

该节点通过 `v4l2-ctl` 直接读取摄像头已经编码好的 JPEG 帧，不使用 OpenCV 或 NumPy，也不在 Pi 上解码和二次编码。

摄像头能力表：

| 格式 | 最大分辨率 | 标称帧率 |
| --- | --- | --- |
| MJPEG | `1920x1080` | 30 fps |
| YUYV | `1920x1080` | 5 fps |

1080p MJPEG 会显著增加 Wi-Fi 和 Foxglove Bridge 带宽。控制和调试默认保留 `640x480@30`；视觉推理应使用独立的低延迟传输和 latest-frame 策略，不应把 Foxglove 当作控制闭环。

## 构建、测试和 Git 记录

相关包已在 Pi 上通过 `colcon build --symlink-install` 构建。硬件协议、fake serial、bringup launch、模式管理和 MJPEG 分帧测试均已通过。

已推送分支和提交：

```text
feat/ros2-control-arm-modes
  f42bf78 feat(ros2): add managed standby and unload modes

feat/foxglove-camera-stream
  03799dc feat(ros2): stream camera video to Foxglove
  b38d407 perf(ros2): passthrough native camera MJPEG
  4a25dd8 chore(ros2): clean up camera streamer state
```

## 尚未完成和安全边界

- CAD/URDF 高精度 mesh 已下载到工作区，但装配坐标尚未完成验收，相关修改没有进入上述控制和相机提交。
- Foxglove 是监控、调试和操作入口，不是急停系统。
- 视觉结果在驱动机械臂前必须经过时间戳、置信度、工作空间、速度和碰撞检查。
- 真实模式下不能用粗暴杀进程代替受控停机；停止控制前先确保机械臂不会因失去力矩下坠。
- 摄像头 launch 当前独立于真实 bringup，Pi 重启后需要重新启动或另行配置 systemd 服务。
