cmake_minimum_required(VERSION 3.8)
project(zyarm_bringup)

find_package(ament_cmake REQUIRED)

if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(
    test_x1_standard_real_ros2_control_launch
    tests/test_x1_standard_real_ros2_control_launch.py
  )
  ament_add_pytest_test(
    test_camera_streamer
    tests/test_camera_streamer.py
  )
  ament_add_pytest_test(
    test_zyarm_stack
    tests/test_zyarm_stack.py
  )
endif()

install(
  DIRECTORY config launch rviz
  DESTINATION share/${PROJECT_NAME}
)

install(
  PROGRAMS scripts/standby_manager.py
  DESTINATION lib/${PROJECT_NAME}
  RENAME standby_manager
)

install(
  PROGRAMS scripts/camera_streamer.py
  DESTINATION lib/${PROJECT_NAME}
  RENAME camera_streamer
)

install(
  PROGRAMS scripts/zyarm_stack.py
  DESTINATION lib/${PROJECT_NAME}
  RENAME zyarm_stack
)

install(
  PROGRAMS scripts/stack_probe.py
  DESTINATION lib/${PROJECT_NAME}
  RENAME stack_probe
)

ament_package()
