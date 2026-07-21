import { ExtensionContext } from "@foxglove/extension";

import { initClawControlPanel } from "./ClawControlPanel";
import { initMotorTemperaturePanel } from "./MotorTemperaturePanel";

export function activate(extensionContext: ExtensionContext): void {
  extensionContext.registerPanel({
    name: "ZYArm Claw Control",
    initPanel: initClawControlPanel,
  });
  extensionContext.registerPanel({
    name: "ZYArm Motor Temperatures",
    initPanel: initMotorTemperaturePanel,
  });
}
