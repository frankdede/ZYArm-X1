import { ExtensionContext } from "@foxglove/extension";

import { initGripperControlPanel } from "./GripperControlPanel";
import { initMotorTemperaturePanel } from "./MotorTemperaturePanel";

export function activate(extensionContext: ExtensionContext): void {
  extensionContext.registerPanel({
    name: "ZYArm Gripper Control",
    initPanel: initGripperControlPanel,
  });
  extensionContext.registerPanel({
    name: "ZYArm Motor Temperatures",
    initPanel: initMotorTemperaturePanel,
  });
}
