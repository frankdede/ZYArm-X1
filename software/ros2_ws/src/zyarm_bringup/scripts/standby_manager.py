#!/usr/bin/env python3

from __future__ import annotations

import threading
from typing import Iterable

import rclpy
from control_msgs.action import FollowJointTrajectory
from controller_manager_msgs.srv import (
    ListControllers,
    ListHardwareComponents,
    SetHardwareComponentState,
    SwitchController,
)
from lifecycle_msgs.msg import State
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_srvs.srv import Trigger
from trajectory_msgs.msg import JointTrajectoryPoint


class StandbyManager(Node):
    def __init__(self) -> None:
        super().__init__("zyarm_standby_manager")
        self.declare_parameter(
            "controller_manager", "/zyarm_x1_standard_controller_manager"
        )
        self.declare_parameter("hardware_component", "ZyarmX1StandardSystem")
        self.declare_parameter(
            "managed_controllers",
            ["joint_state_broadcaster", "arm_controller", "gripper_controller"],
        )
        self.declare_parameter("mode", "real")
        self.declare_parameter("service_timeout_sec", 5.0)
        self.declare_parameter("standby_timeout_sec", 35.0)
        self.declare_parameter("sim_motion_duration_sec", 4.0)

        manager = str(self.get_parameter("controller_manager").value).rstrip("/")
        self._hardware_component = str(self.get_parameter("hardware_component").value)
        self._managed_controllers = list(
            self.get_parameter("managed_controllers").value
        )
        self._mode = str(self.get_parameter("mode").value).strip().lower()
        self._service_timeout = float(self.get_parameter("service_timeout_sec").value)
        self._standby_timeout = float(self.get_parameter("standby_timeout_sec").value)
        self._sim_motion_duration = float(
            self.get_parameter("sim_motion_duration_sec").value
        )
        if self._mode not in {"real", "sim"}:
            raise ValueError("mode must be 'real' or 'sim'")
        self._callback_group = ReentrantCallbackGroup()
        self._operation_lock = threading.Lock()
        self._unloaded = False
        self._controllers_before_unload: list[str] = []

        self._list_client = self.create_client(
            ListControllers, f"{manager}/list_controllers", callback_group=self._callback_group
        )
        self._switch_client = self.create_client(
            SwitchController, f"{manager}/switch_controller", callback_group=self._callback_group
        )
        self._hardware_client = self.create_client(
            SetHardwareComponentState,
            f"{manager}/set_hardware_component_state",
            callback_group=self._callback_group,
        )
        self._list_hardware_client = self.create_client(
            ListHardwareComponents,
            f"{manager}/list_hardware_components",
            callback_group=self._callback_group,
        )
        self._standby_raw_client = self.create_client(
            Trigger, "/zyarm/standby_raw", callback_group=self._callback_group
        )
        self._reset_raw_client = self.create_client(
            Trigger, "/zyarm/reset_raw", callback_group=self._callback_group
        )
        self._unload_raw_client = self.create_client(
            Trigger, "/zyarm/unload_raw", callback_group=self._callback_group
        )
        self._arm_action = ActionClient(
            self,
            FollowJointTrajectory,
            "/arm_controller/follow_joint_trajectory",
            callback_group=self._callback_group,
        )
        self._gripper_action = ActionClient(
            self,
            FollowJointTrajectory,
            "/gripper_controller/follow_joint_trajectory",
            callback_group=self._callback_group,
        )
        self._standby_service = self.create_service(
            Trigger,
            "/zyarm/standby",
            self._handle_standby,
            callback_group=self._callback_group,
        )
        self._reset_service = self.create_service(
            Trigger,
            "/zyarm/reset",
            self._handle_reset,
            callback_group=self._callback_group,
        )
        self._unload_service = self.create_service(
            Trigger,
            "/zyarm/unload",
            self._handle_unload,
            callback_group=self._callback_group,
        )
        self._resume_service = self.create_service(
            Trigger,
            "/zyarm/resume",
            self._handle_resume,
            callback_group=self._callback_group,
        )

    def _wait_for_client(self, client, name: str) -> None:
        if not client.wait_for_service(timeout_sec=self._service_timeout):
            raise RuntimeError(f"Required service is unavailable: {name}")

    @staticmethod
    def _wait_future(future, timeout: float):
        completed = threading.Event()
        future.add_done_callback(lambda _: completed.set())
        if not completed.wait(timeout):
            raise TimeoutError("ROS service call timed out")
        exception = future.exception()
        if exception is not None:
            raise exception
        return future.result()

    def _call(self, client, request, name: str, timeout: float | None = None):
        self._wait_for_client(client, name)
        return self._wait_future(
            client.call_async(request), self._service_timeout if timeout is None else timeout
        )

    def _active_controllers(self) -> list[str]:
        response = self._call(
            self._list_client, ListControllers.Request(), "list_controllers"
        )
        return [controller.name for controller in response.controller if controller.state == "active"]

    def _inactive_managed_controllers(self) -> list[str]:
        response = self._call(
            self._list_client, ListControllers.Request(), "list_controllers"
        )
        states = {controller.name: controller.state for controller in response.controller}
        return [
            name for name in self._managed_controllers if states.get(name) == "inactive"
        ]

    def _hardware_state(self) -> int:
        response = self._call(
            self._list_hardware_client,
            ListHardwareComponents.Request(),
            "list_hardware_components",
        )
        for component in response.component:
            if component.name == self._hardware_component:
                return component.state.id
        raise RuntimeError(f"Hardware component not found: {self._hardware_component}")

    def _switch(self, *, activate: Iterable[str] = (), deactivate: Iterable[str] = ()) -> None:
        request = SwitchController.Request()
        request.activate_controllers = list(activate)
        request.deactivate_controllers = list(deactivate)
        request.strictness = SwitchController.Request.STRICT
        request.activate_asap = True
        request.timeout.sec = int(self._service_timeout)
        response = self._call(self._switch_client, request, "switch_controller")
        if not response.ok:
            raise RuntimeError(response.message or "Controller switch failed")

    def _set_hardware_state(self, state_id: int, label: str) -> None:
        request = SetHardwareComponentState.Request()
        request.name = self._hardware_component
        request.target_state = State(id=state_id, label=label)
        response = self._call(
            self._hardware_client, request, "set_hardware_component_state"
        )
        if not response.ok:
            raise RuntimeError(
                f"Failed to set {self._hardware_component} to {label}; "
                f"current state is {response.state.label}"
            )

    def _send_sim_goal(self, client, joint_names: list[str], positions: list[float]):
        if not client.wait_for_server(timeout_sec=self._service_timeout):
            raise RuntimeError(f"Trajectory action is unavailable for {joint_names}")
        goal = FollowJointTrajectory.Goal()
        goal.trajectory.joint_names = joint_names
        point = JointTrajectoryPoint()
        point.positions = positions
        duration_ns = round(self._sim_motion_duration * 1_000_000_000)
        point.time_from_start.sec = duration_ns // 1_000_000_000
        point.time_from_start.nanosec = duration_ns % 1_000_000_000
        goal.trajectory.points = [point]
        goal_handle = self._wait_future(
            client.send_goal_async(goal), self._service_timeout
        )
        if not goal_handle.accepted:
            raise RuntimeError(f"Trajectory goal was rejected for {joint_names}")
        return goal_handle

    def _run_sim_standby(self) -> None:
        # CMD38's ToC target [0, -105, 90, 0, 0, 0, 0] mapped into ROS units.
        arm_goal = self._send_sim_goal(
            self._arm_action,
            ["joint0", "joint1", "joint2", "joint3", "joint4", "joint5"],
            [0.0, 1.3089969389957472, 0.0, 0.0, 0.0, 0.0],
        )
        gripper_goal = self._send_sim_goal(
            self._gripper_action, ["joint6"], [0.0]
        )
        result_timeout = self._sim_motion_duration + self._service_timeout
        for goal_handle in (arm_goal, gripper_goal):
            wrapped_result = self._wait_future(
                goal_handle.get_result_async(), result_timeout
            )
            result = wrapped_result.result
            if result.error_code != FollowJointTrajectory.Result.SUCCESSFUL:
                raise RuntimeError(
                    result.error_string or f"Trajectory failed with code {result.error_code}"
                )

    def _handle_standby(self, _request, response):
        if not self._operation_lock.acquire(blocking=False):
            response.success = False
            response.message = "Another arm mode operation is already in progress"
            return response

        active_controllers: list[str] = []
        hardware_inactive = False
        raw_command_started = False
        try:
            if self._unloaded:
                raise RuntimeError("Arm is unloaded; call /zyarm/resume before standby")

            if self._mode == "sim":
                self._run_sim_standby()
                response.success = True
                response.message = "Simulated standby trajectory completed"
                return response

            active_controllers = self._active_controllers()
            if active_controllers:
                self._switch(deactivate=active_controllers)

            self._set_hardware_state(State.PRIMARY_STATE_INACTIVE, "inactive")
            hardware_inactive = True

            self._wait_for_client(self._standby_raw_client, "/zyarm/standby_raw")
            raw_command_started = True
            raw_response = self._wait_future(
                self._standby_raw_client.call_async(Trigger.Request()),
                self._standby_timeout,
            )
            if not raw_response.success:
                raise RuntimeError(raw_response.message)

            self._set_hardware_state(State.PRIMARY_STATE_ACTIVE, "active")
            hardware_inactive = False
            if active_controllers:
                self._switch(activate=active_controllers)

            response.success = True
            response.message = "CMD38 standby completed and ros2_control was resynchronized"
        except Exception as exc:
            # An ACK timeout can mean that CMD38 is still moving the arm. Keep control inactive.
            if not raw_command_started:
                try:
                    if hardware_inactive:
                        self._set_hardware_state(State.PRIMARY_STATE_ACTIVE, "active")
                    if active_controllers:
                        self._switch(activate=active_controllers)
                except Exception as recovery_exc:
                    self.get_logger().error(f"Standby recovery failed: {recovery_exc}")
            response.success = False
            response.message = str(exc)
            self.get_logger().error(f"Standby failed: {exc}")
        finally:
            self._operation_lock.release()
        return response

    def _handle_reset(self, _request, response):
        if not self._operation_lock.acquire(blocking=False):
            response.success = False
            response.message = "Another arm mode operation is already in progress"
            return response

        active_controllers: list[str] = []
        controllers_to_activate: list[str] = []
        hardware_inactive = False
        hardware_was_active = False
        raw_command_started = False
        try:
            if self._unloaded:
                raise RuntimeError("Arm is unloaded; call /zyarm/resume before reset")

            if self._mode == "sim":
                response.success = True
                response.message = "Simulated reset completed (no firmware command sent)"
                return response

            active_controllers = self._active_controllers()
            controllers_to_activate = active_controllers + [
                name
                for name in self._inactive_managed_controllers()
                if name not in active_controllers
            ]
            if active_controllers:
                self._switch(deactivate=active_controllers)

            hardware_state = self._hardware_state()
            hardware_was_active = hardware_state == State.PRIMARY_STATE_ACTIVE
            if hardware_state != State.PRIMARY_STATE_INACTIVE:
                self._set_hardware_state(State.PRIMARY_STATE_INACTIVE, "inactive")
            hardware_inactive = True

            self._wait_for_client(self._reset_raw_client, "/zyarm/reset_raw")
            raw_command_started = True
            raw_response = self._wait_future(
                self._reset_raw_client.call_async(Trigger.Request()),
                self._standby_timeout,
            )
            if not raw_response.success:
                raise RuntimeError(raw_response.message)

            self._set_hardware_state(State.PRIMARY_STATE_ACTIVE, "active")
            hardware_inactive = False
            if controllers_to_activate:
                self._switch(activate=controllers_to_activate)

            response.success = True
            response.message = "CMD1 reset completed and ros2_control was resynchronized"
        except Exception as exc:
            if not raw_command_started:
                try:
                    if hardware_inactive and hardware_was_active:
                        self._set_hardware_state(State.PRIMARY_STATE_ACTIVE, "active")
                    if active_controllers:
                        self._switch(activate=active_controllers)
                except Exception as recovery_exc:
                    self.get_logger().error(f"Reset recovery failed: {recovery_exc}")
            response.success = False
            response.message = str(exc)
            self.get_logger().error(f"Reset failed: {exc}")
        finally:
            self._operation_lock.release()
        return response

    def _handle_unload(self, _request, response):
        if not self._operation_lock.acquire(blocking=False):
            response.success = False
            response.message = "Another arm mode operation is already in progress"
            return response

        active_controllers: list[str] = []
        hardware_inactive = False
        raw_command_started = False
        try:
            if self._unloaded:
                response.success = True
                response.message = "Arm is already unloaded"
                return response

            active_controllers = self._active_controllers()
            if active_controllers:
                self._switch(deactivate=active_controllers)

            self._set_hardware_state(State.PRIMARY_STATE_INACTIVE, "inactive")
            hardware_inactive = True

            if self._mode == "real":
                self._wait_for_client(self._unload_raw_client, "/zyarm/unload_raw")
                raw_command_started = True
                raw_response = self._wait_future(
                    self._unload_raw_client.call_async(Trigger.Request()),
                    self._standby_timeout,
                )
                if not raw_response.success:
                    raise RuntimeError(raw_response.message)

            self._controllers_before_unload = active_controllers
            self._unloaded = True
            response.success = True
            response.message = (
                "CMD23 unload completed; hardware and controllers remain inactive"
                if self._mode == "real"
                else "Simulated unload completed; hardware and controllers are inactive"
            )
        except Exception as exc:
            # Once CMD23 may have started, restoring control could unexpectedly lock the arm.
            if not raw_command_started:
                try:
                    if hardware_inactive:
                        self._set_hardware_state(State.PRIMARY_STATE_ACTIVE, "active")
                    if active_controllers:
                        self._switch(activate=active_controllers)
                except Exception as recovery_exc:
                    self.get_logger().error(f"Unload recovery failed: {recovery_exc}")
            response.success = False
            response.message = str(exc)
            self.get_logger().error(f"Unload failed: {exc}")
        finally:
            self._operation_lock.release()
        return response

    def _handle_resume(self, _request, response):
        if not self._operation_lock.acquire(blocking=False):
            response.success = False
            response.message = "Another arm mode operation is already in progress"
            return response

        try:
            if not self._unloaded:
                response.success = True
                response.message = "Arm is not unloaded; no resume was needed"
                return response

            self._set_hardware_state(State.PRIMARY_STATE_ACTIVE, "active")
            if self._controllers_before_unload:
                self._switch(activate=self._controllers_before_unload)

            restored_controllers = self._controllers_before_unload
            self._controllers_before_unload = []
            self._unloaded = False
            response.success = True
            response.message = (
                "Hardware resumed and controllers restored: "
                + ", ".join(restored_controllers)
            )
        except Exception as exc:
            response.success = False
            response.message = str(exc)
            self.get_logger().error(f"Resume failed: {exc}")
        finally:
            self._operation_lock.release()
        return response


def main(args=None) -> None:
    rclpy.init(args=args)
    node = StandbyManager()
    executor = MultiThreadedExecutor(num_threads=3)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            executor.shutdown()
            node.destroy_node()
        except KeyboardInterrupt:
            pass
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
