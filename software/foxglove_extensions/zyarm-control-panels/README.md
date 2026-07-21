# ZYArm Foxglove control panels

This extension adds two panels:

- `ZYArm Motor Temperatures` displays servo temperatures S1 through S9.
- `ZYArm Claw Control` publishes guarded `joint6` trajectories to the real or simulated
  `gripper_controller`.

Build and install into the local Foxglove desktop application:

```bash
npm install
npm run local-install
```

The claw range is `0..34 mm`. Open and Close only change the target. The panel publishes a
trajectory only after `Enable control` is selected and `Send target` is clicked. The controller
must be active before it can execute the command.
