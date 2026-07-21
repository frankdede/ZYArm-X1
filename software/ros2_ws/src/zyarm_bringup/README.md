`zyarm_bringup` 负责把描述、仿真、硬件实例运行时和 ros2_control 控制链整理成系统入口。

## 一键管理真机、Foxglove 和摄像头

先构建 workspace，然后使用 venv 安装 service、Python 依赖和 CLI wrapper：

```bash
source /home/frank/venv/bin/activate
python3 software/ros2_ws/src/zyarm_bringup/scripts/install_stack_service.py \
  --venv /home/frank/venv
```

installer 只向指定 venv 安装 `venv-requirements.txt` 中的依赖，不修改系统 Python。它会写入 `/etc/default/zyarm-stack`，systemd 和 `/usr/local/bin/zyarm-stack` 都固定使用同一 venv。

安装后使用：

```bash
zyarm-stack status
zyarm-stack start --confirm-safe
zyarm-stack logs -f
zyarm-stack stop --confirm-safe
```

`start`、`stop` 和 `restart` 必须显式提供 `--confirm-safe`。启动前需要支撑机械臂、清空工作区并确认人员已离开。该 service 默认不启用开机自启动；Foxglove Bridge 继续由现有 `foxglove-bridge.service` 管理。

真机启动采用两阶段流程。`zyarm-stack start --confirm-safe` 会打开串口、启动 Foxglove、模式服务和摄像头，但硬件与 controller 保持 `inactive`，因此即使固件已经处于 standby，`/zyarm/reset` 仍然可用。确认机械臂已放稳、工作区无人后，在 Foxglove 调用 `std_srvs/srv/Trigger` 类型的 `/zyarm/reset`；CMD1 完成、当前位置同步成功后，硬件和三个 controller 才会变为 `active`。

`zyarm-stack status` 显示 `recovery-ready` 表示服务均已上线、正在等待显式 reset；显示 `active` 才能接收轨迹。`/zyarm/resume` 只恢复同一次运行中由 `/zyarm/unload` 停掉的 controller，不用于退出固件 standby。

当前保留的入口：

- `bringup_x1_standard_ros2_control.launch.py`
  启动基础 ros2_control 控制链，适合验证模型、controller manager 和控制器配置。
- `bringup_x1_plus_ros2_control.launch.py`
  启动 Plus 模型的基础 ros2_control 控制链，适合无串口验证模型、controller manager 和控制器配置。
- `bringup_x1_standard_real_ros2_control.launch.py`
  启动真机 ros2_control 底层控制链，不启动 MoveIt；适合单独验证 controller manager、控制器和 `/joint_states`。
- `bringup_x1_plus_real_ros2_control.launch.py`
  启动 Plus 模型的真机 ros2_control 底层控制链，不启动 MoveIt；操作方式、指令、接口和电机语义参考 `x1_standard`。
- `teleop_only_system.launch.py`
  启动 `zyarm_hardware/arm_system.launch.py`，默认读取 `zyarm_hardware/config/teleop_pair_real.yaml`，用于纯 ROS 主从遥操调试。

职责边界：

- `zyarm_hardware`
  负责多机械臂实例、实例关系和硬件服务/话题契约。
- `zyarm_hardware_interface`
  负责 MoveIt 真机 ros2_control 控制链路中的硬件插件。
- `zyarm_bringup`
  只负责场景级 launch 组合，不承载学习、数据集或策略评估逻辑。
