import { PanelExtensionContext } from "@foxglove/extension";
import { CSSProperties, ReactElement, useEffect, useLayoutEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";

const SERVO_COUNT = 9;
const WARN_C = 60;
const ERROR_C = 70;
const TEMPERATURE_TOPICS = Array.from(
  { length: SERVO_COUNT },
  (_, index) => `/zyarm/motors/servo_${index + 1}/temperature`,
);

type TemperatureMessage = {
  temperature?: number;
};

function palette(colorScheme: "dark" | "light") {
  return colorScheme === "dark"
    ? {
        background: "#151719",
        surface: "#202428",
        border: "#3b4248",
        text: "#f4f6f7",
        muted: "#aab2b8",
      }
    : {
        background: "#f5f7f8",
        surface: "#ffffff",
        border: "#cbd2d7",
        text: "#182026",
        muted: "#5c6973",
      };
}

function readingColor(value: number | undefined): string {
  if (value == undefined) {
    return "#7b8790";
  }
  if (value >= ERROR_C) {
    return "#d64545";
  }
  if (value >= WARN_C) {
    return "#d18b18";
  }
  return "#24945b";
}

function MotorTemperaturePanel({ context }: { context: PanelExtensionContext }): ReactElement {
  const [readings, setReadings] = useState<Array<number | undefined>>(
    Array.from({ length: SERVO_COUNT }),
  );
  const [colorScheme, setColorScheme] = useState<"dark" | "light">("dark");
  const [renderDone, setRenderDone] = useState<(() => void) | undefined>();

  useLayoutEffect(() => {
    context.onRender = (renderState, done) => {
      if (renderState.colorScheme != undefined) {
        setColorScheme(renderState.colorScheme);
      }
      if (renderState.currentFrame != undefined) {
        setReadings((previous) => {
          const next = [...previous];
          for (const event of renderState.currentFrame ?? []) {
            const index = TEMPERATURE_TOPICS.indexOf(event.topic);
            if (index < 0) {
              continue;
            }
            const message = event.message as TemperatureMessage;
            if (typeof message.temperature === "number" && Number.isFinite(message.temperature)) {
              next[index] = message.temperature;
            }
          }
          return next;
        });
      }
      setRenderDone(() => done);
    };
    context.watch("currentFrame");
    context.watch("colorScheme");
    context.subscribe(TEMPERATURE_TOPICS.map((topic) => ({ topic })));

    return () => {
      context.unsubscribeAll();
      context.onRender = undefined;
    };
  }, [context]);

  useEffect(() => {
    renderDone?.();
  }, [renderDone]);

  const colors = palette(colorScheme);
  const maximum = useMemo(() => {
    const available = readings.filter((value): value is number => value != undefined);
    return available.length > 0 ? Math.max(...available) : undefined;
  }, [readings]);

  const containerStyle: CSSProperties = {
    boxSizing: "border-box",
    minHeight: "100%",
    padding: 12,
    background: colors.background,
    color: colors.text,
    fontFamily: "Inter, system-ui, sans-serif",
  };

  return (
    <div style={containerStyle}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 650 }}>Motor temperatures</h2>
        <span style={{ color: readingColor(maximum), fontSize: 13, fontWeight: 650 }}>
          MAX {maximum == undefined ? "--" : `${maximum.toFixed(1)} C`}
        </span>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(88px, 1fr))",
          gap: 8,
          marginTop: 12,
        }}
      >
        {readings.map((value, index) => (
          <div
            key={index}
            style={{
              minHeight: 68,
              padding: 10,
              border: `1px solid ${colors.border}`,
              borderLeft: `4px solid ${readingColor(value)}`,
              borderRadius: 6,
              background: colors.surface,
              boxSizing: "border-box",
            }}
          >
            <div style={{ color: colors.muted, fontSize: 12 }}>S{index + 1}</div>
            <div style={{ marginTop: 8, fontSize: 19, fontWeight: 700 }}>
              {value == undefined ? "--" : `${value.toFixed(1)} C`}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function initMotorTemperaturePanel(context: PanelExtensionContext): () => void {
  const root = createRoot(context.panelElement);
  root.render(<MotorTemperaturePanel context={context} />);
  return () => {
    root.unmount();
  };
}
