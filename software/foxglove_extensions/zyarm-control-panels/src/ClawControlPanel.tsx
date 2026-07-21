import { PanelExtensionContext } from "@foxglove/extension";
import { CSSProperties, ReactElement, useCallback, useEffect, useLayoutEffect, useState } from "react";
import { createRoot } from "react-dom/client";

const COMMAND_TOPIC = "/gripper_controller/joint_trajectory";
const COMMAND_SCHEMA = "trajectory_msgs/msg/JointTrajectory";
const JOINT_STATE_TOPIC = "/joint_states";
const CLAW_TRAVEL_MM = 34;

type JointStateMessage = {
  name?: readonly string[];
  position?: readonly number[];
};

function palette(colorScheme: "dark" | "light") {
  return colorScheme === "dark"
    ? {
        background: "#151719",
        surface: "#202428",
        border: "#3b4248",
        text: "#f4f6f7",
        muted: "#aab2b8",
        accent: "#1f9d68",
      }
    : {
        background: "#f5f7f8",
        surface: "#ffffff",
        border: "#cbd2d7",
        text: "#182026",
        muted: "#5c6973",
        accent: "#16794e",
      };
}

function ClawControlPanel({ context }: { context: PanelExtensionContext }): ReactElement {
  const [targetMm, setTargetMm] = useState(0);
  const [durationSec, setDurationSec] = useState(1);
  const [currentMm, setCurrentMm] = useState<number | undefined>();
  const [controlEnabled, setControlEnabled] = useState(false);
  const [status, setStatus] = useState("Idle");
  const [colorScheme, setColorScheme] = useState<"dark" | "light">("dark");
  const [renderDone, setRenderDone] = useState<(() => void) | undefined>();
  const publishAvailable = context.advertise != undefined && context.publish != undefined;

  useLayoutEffect(() => {
    context.advertise?.(COMMAND_TOPIC, COMMAND_SCHEMA);
    context.onRender = (renderState, done) => {
      if (renderState.colorScheme != undefined) {
        setColorScheme(renderState.colorScheme);
      }
      for (const event of renderState.currentFrame ?? []) {
        if (event.topic !== JOINT_STATE_TOPIC) {
          continue;
        }
        const message = event.message as JointStateMessage;
        const index = message.name?.indexOf("joint6") ?? -1;
        const position = index >= 0 ? message.position?.[index] : undefined;
        if (position != undefined && Number.isFinite(position)) {
          setCurrentMm(position * 1000);
        }
      }
      setRenderDone(() => done);
    };
    context.watch("currentFrame");
    context.watch("colorScheme");
    context.subscribe([{ topic: JOINT_STATE_TOPIC }]);

    return () => {
      context.unsubscribeAll();
      context.unadvertise?.(COMMAND_TOPIC);
      context.onRender = undefined;
    };
  }, [context]);

  useEffect(() => {
    renderDone?.();
  }, [renderDone]);

  const sendCommand = useCallback(() => {
    if (!controlEnabled) {
      setStatus("Control is not enabled");
      return;
    }
    if (context.publish == undefined) {
      setStatus("Publishing unavailable");
      return;
    }

    const safeTargetMm = Math.min(CLAW_TRAVEL_MM, Math.max(0, targetMm));
    const safeDurationSec = Math.min(10, Math.max(0.2, durationSec));
    const seconds = Math.floor(safeDurationSec);
    const nanoseconds = Math.round((safeDurationSec - seconds) * 1_000_000_000);
    context.publish(COMMAND_TOPIC, {
      header: { stamp: { sec: 0, nanosec: 0 }, frame_id: "" },
      joint_names: ["joint6"],
      points: [
        {
          positions: [safeTargetMm / 1000],
          velocities: [],
          accelerations: [],
          effort: [],
          time_from_start: { sec: seconds, nanosec: nanoseconds },
        },
      ],
    });
    setStatus(`Sent ${safeTargetMm.toFixed(1)} mm`);
  }, [context, controlEnabled, durationSec, targetMm]);

  const colors = palette(colorScheme);
  const buttonStyle: CSSProperties = {
    minHeight: 34,
    border: `1px solid ${colors.border}`,
    borderRadius: 5,
    background: colors.surface,
    color: colors.text,
    cursor: "pointer",
    fontWeight: 600,
  };

  return (
    <div
      style={{
        boxSizing: "border-box",
        minHeight: "100%",
        padding: 12,
        background: colors.background,
        color: colors.text,
        fontFamily: "Inter, system-ui, sans-serif",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 650 }}>Claw control</h2>
        <span style={{ color: colors.muted, fontSize: 13 }}>
          {currentMm == undefined ? "--" : `${currentMm.toFixed(1)} mm`}
        </span>
      </div>

      <div style={{ marginTop: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
          <span style={{ color: colors.muted }}>Target</span>
          <strong>{targetMm.toFixed(1)} mm</strong>
        </div>
        <input
          aria-label="Claw target"
          type="range"
          min={0}
          max={CLAW_TRAVEL_MM}
          step={0.5}
          value={targetMm}
          onChange={(event) => {
            setTargetMm(Number(event.target.value));
          }}
          style={{ width: "100%", marginTop: 8, accentColor: colors.accent }}
        />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginTop: 10 }}>
        <button
          type="button"
          style={buttonStyle}
          onClick={() => {
            setTargetMm(0);
          }}
        >
          Close
        </button>
        <button
          type="button"
          style={buttonStyle}
          onClick={() => {
            setTargetMm(CLAW_TRAVEL_MM);
          }}
        >
          Open
        </button>
      </div>

      <label
        style={{ display: "grid", gridTemplateColumns: "1fr 76px", gap: 10, marginTop: 14 }}
      >
        <span style={{ color: colors.muted, fontSize: 12, alignSelf: "center" }}>Duration</span>
        <input
          aria-label="Trajectory duration"
          type="number"
          min={0.2}
          max={10}
          step={0.1}
          value={durationSec}
          onChange={(event) => {
            setDurationSec(Number(event.target.value));
          }}
          style={{
            minWidth: 0,
            padding: "6px 7px",
            border: `1px solid ${colors.border}`,
            borderRadius: 4,
            background: colors.surface,
            color: colors.text,
          }}
        />
      </label>

      <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 14, fontSize: 13 }}>
        <input
          type="checkbox"
          checked={controlEnabled}
          onChange={(event) => {
            setControlEnabled(event.target.checked);
          }}
          style={{ accentColor: colors.accent }}
        />
        Enable control
      </label>

      <button
        type="button"
        disabled={!controlEnabled || !publishAvailable}
        onClick={sendCommand}
        style={{
          ...buttonStyle,
          width: "100%",
          marginTop: 12,
          borderColor: colors.accent,
          background: controlEnabled && publishAvailable ? colors.accent : colors.surface,
          color: controlEnabled && publishAvailable ? "#ffffff" : colors.muted,
          cursor: controlEnabled && publishAvailable ? "pointer" : "not-allowed",
        }}
      >
        Send target
      </button>
      <div style={{ marginTop: 9, color: colors.muted, fontSize: 12 }}>{status}</div>
    </div>
  );
}

export function initClawControlPanel(context: PanelExtensionContext): () => void {
  const root = createRoot(context.panelElement);
  root.render(<ClawControlPanel context={context} />);
  return () => {
    root.unmount();
  };
}
