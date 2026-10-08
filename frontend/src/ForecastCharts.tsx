import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { init, use, type EChartsCoreOption } from "echarts/core";
import { LineChart, BarChart, ScatterChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  AxisPointerComponent,
  MarkLineComponent,
  TitleComponent,
  AriaComponent,
} from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import { LabelLayout } from "echarts/features";
import type { Climate, EnsembleForecast, ModelForecast } from "./ForecastPage";

use([
  LineChart,
  BarChart,
  ScatterChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  AxisPointerComponent,
  MarkLineComponent,
  TitleComponent,
  AriaComponent,
  LabelLayout,
  SVGRenderer,
]);

// Validated categorical sets (CVD-safe on white): orange/red, blue, violet.
const ORANGE = "#eb6834";
const RED = "#e34948";
const BLUE = "#2a78d6";
const VIOLET = "#4a3aa7";
const MEMBER = "rgba(98, 110, 128, 0.28)";
const INK = "#5c6b7e";
const CLIMATE = "#1f2937";
const GRID_LINE = "#e8ecf2";

const dayLabel = new Intl.DateTimeFormat("es-ES", {
  weekday: "short",
  day: "numeric",
  timeZone: "Europe/Madrid",
});
const hourLabel = new Intl.DateTimeFormat("es-ES", {
  weekday: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Europe/Madrid",
});

type Panel = {
  title?: string;
  unit: string;
  height: number;
  min?: number;
  max?: number;
  /** A strip of symbols (sky, wind arrows): no y-axis, no title. */
  bare?: boolean;
  /** Approximate number of y-axis intervals (fewer for short panels). */
  ticks?: number;
  /** Secondary y-axis on the right, as in classic meteograms (precipitation). */
  right?: { unit: string; max: number };
};
type Series = Record<string, unknown> & {
  panel: number;
  unit: string;
  legend?: boolean;
  right?: boolean;
};

function pairs(time: number[], values: (number | null)[]) {
  return time.map((t, i) => [t * 1000, values[i] ?? null]);
}

function fmt(value: unknown, unit: string) {
  return typeof value === "number"
    ? `${value.toLocaleString("es-ES", { maximumFractionDigits: 1 })} ${unit}`
    : "sin dato";
}

const axisNumber = (value: number) => value.toLocaleString("es-ES", { maximumFractionDigits: 1 });

/** Height of the date strip above the first panel (day labels at the top as well). */
export const TOP_DATES = 20;

/** Stacked panels sharing one time axis and crosshair; titled panels get their own legend row. */
function panelOption(panels: Panel[], series: Series[], range: [number, number]): EChartsCoreOption {
  let y = TOP_DATES;
  const grids = panels.map((panel) => {
    y += panel.title ? 44 : 4;
    const grid = { left: 44, right: panels.some((p) => p.right) ? 40 : 14, top: y, height: panel.height };
    y += panel.height;
    return grid;
  });
  const secondary = new Map<number, number>();
  panels.forEach((panel, i) => {
    if (panel.right) secondary.set(i, panels.length + secondary.size);
  });
  const now = Date.now();
  const last = panels.length - 1;
  const days = {
    type: "time",
    min: range[0],
    max: range[1],
    // One split line per day (the browser's local midnight), as in classic meteograms.
    minInterval: 86400000,
    maxInterval: 86400000,
    axisTick: { show: false },
  };
  const dayNames = { color: INK, fontSize: 10, hideOverlap: true, formatter: (value: number) => dayLabel.format(value) };
  // An empty one-pixel grid that only carries the top row of day labels.
  const strip = panels.length;
  return {
    animation: false,
    aria: { enabled: true },
    textStyle: { fontFamily: "Inter, ui-sans-serif, sans-serif" },
    title: panels.flatMap((panel, i) =>
      panel.title
        ? [
            {
              text: `${panel.title} (${panel.unit}${panel.right ? ` · ${panel.right.unit}` : ""})`,
              top: grids[i].top - 42,
              left: 44,
              textStyle: { fontSize: 11, fontWeight: 600, color: INK },
            },
          ]
        : [],
    ),
    legend: panels
      .map((panel, i) => ({
        top: grids[i].top - 24,
        left: 40,
        right: 8,
        itemWidth: 14,
        itemHeight: 8,
        itemGap: 12,
        textStyle: { fontSize: 11, color: INK },
        data: panel.title
          ? series.filter((s) => s.panel === i && s.legend !== false).map((s) => s.name as string)
          : [],
      }))
      .filter((legend) => legend.data.length > 1),
    grid: [...grids, { left: grids[0].left, right: grids[0].right, top: TOP_DATES - 1, height: 1 }],
    axisPointer: { link: [{ xAxisIndex: "all" }], label: { show: false } },
    tooltip: {
      trigger: "axis",
      confine: true,
      renderMode: "richText",
      formatter: (
        items: {
          axisValue: number;
          seriesName: string;
          value: [number, number | null];
          seriesIndex: number;
          data: unknown;
        }[],
      ) => {
        if (!items.length) return "";
        const lines = items
          .filter((item) => series[item.seriesIndex]?.legend !== false)
          .map((item) => {
            // Values drawn clipped at the top of the axis keep their real value here.
            const real = (item.data as { real?: number | null } | null)?.real;
            const value = real !== undefined ? real : item.value[1];
            return `${item.seriesName}: ${fmt(value, series[item.seriesIndex].unit)}`;
          });
        return [hourLabel.format(items[0].axisValue), ...lines].join("\n");
      },
    },
    xAxis: [
      ...panels.map((panel, i) => ({
        ...days,
        gridIndex: i,
        axisLine: { show: !panel.bare, lineStyle: { color: "#c4ccd8" } },
        splitLine: { show: true, lineStyle: { color: panel.bare ? "transparent" : GRID_LINE } },
        axisLabel: { ...dayNames, show: i === last },
      })),
      {
        ...days,
        gridIndex: strip,
        position: "top",
        axisLine: { show: false },
        splitLine: { show: false },
        axisPointer: { show: false },
        axisLabel: { ...dayNames, margin: 4 },
      },
    ],
    yAxis: [
      ...panels.map((panel, i) => ({
        type: "value",
        gridIndex: i,
        show: !panel.bare,
        scale: panel.min === undefined,
        min: panel.bare ? 0 : panel.min,
        max: panel.bare ? 1 : panel.max,
        splitNumber: panel.ticks ?? 4,
        axisLabel: { color: INK, fontSize: 10, formatter: axisNumber },
        splitLine: { show: !panel.bare, lineStyle: { color: GRID_LINE } },
      })),
      ...[...secondary.keys()].map((i) => ({
        type: "value",
        gridIndex: i,
        position: "right",
        min: 0,
        max: panels[i].right!.max,
        splitNumber: 4,
        axisLabel: { color: BLUE, fontSize: 10, formatter: axisNumber },
        splitLine: { show: false },
      })),
      { type: "value", gridIndex: strip, show: false },
    ],
    series: series.map(({ panel, unit: _unit, legend: _legend, right, ...rest }, index) => ({
      xAxisIndex: panel,
      yAxisIndex: right ? secondary.get(panel) : panel,
      connectNulls: false,
      showSymbol: false,
      symbolSize: 8,
      smooth: false,
      emphasis: { disabled: true },
      ...rest,
      // A dashed "now" reference in every plotted panel, drawn once per panel.
      markLine:
        !panels[panel].bare &&
        series.findIndex((s) => s.panel === panel) === index &&
        now > range[0] &&
        now < range[1]
          ? {
              silent: true,
              symbol: "none",
              label: { show: false },
              lineStyle: { color: "#9aa5b4", type: "dashed", width: 1 },
              data: [{ xAxis: now }],
            }
          : undefined,
    })),
  };
}

function useChart(option: EChartsCoreOption, height: number) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!root.current) return;
    const chart = init(root.current, undefined, { renderer: "svg", height });
    chart.setOption(option);
    const resize = new ResizeObserver(() => chart.resize({ height }));
    resize.observe(root.current);
    return () => {
      resize.disconnect();
      chart.dispose();
    };
  }, [option, height]);
  return root;
}

/** Hours between sky symbols and wind arrows, from the chart's own width. */
function useSymbolStep(holder: RefObject<HTMLDivElement | null>) {
  const [step, setStep] = useState(12);
  useEffect(() => {
    if (!holder.current) return;
    const observer = new ResizeObserver(([entry]) => {
      const width = entry.contentRect.width;
      setStep(width >= 900 ? 6 : width >= 560 ? 12 : 24);
    });
    observer.observe(holder.current);
    return () => observer.disconnect();
  }, [holder]);
  return step;
}

function line(color: string, width = 2, dashed: false | "dashed" | "dotted" = false) {
  return { type: "line", color, lineStyle: { color, width, type: dashed || "solid" } };
}

const localHour = new Intl.DateTimeFormat("es-ES", { hour: "numeric", hourCycle: "h23", timeZone: "Europe/Madrid" });

/** A sky symbol per 6 h window from model cloud layers and precipitation; high cloud counts less. */
function skySymbol(cloud: number | null, rain: number | null, snow: number | null, middle: number) {
  if (cloud === null || rain === null) return "";
  const hour = Number(localHour.format(middle));
  const night = hour < 7 || hour >= 21;
  if (rain >= 0.5) return (snow ?? 0) >= 0.5 ? "🌨️" : "🌧️";
  if (rain >= 0.1) return night ? "🌧️" : "🌦️";
  if (cloud < 20) return night ? "🌙" : "☀️";
  if (cloud < 50) return night ? "🌙" : "🌤️";
  if (cloud < 80) return "⛅";
  return "☁️";
}

export function Meteogram({ model, range }: { model: ModelForecast; range: [number, number] }) {
  const s = model.series;
  const t = model.time;
  const rain = model.accumulated_6h;
  const holder = useRef<HTMLDivElement>(null);
  const every = useSymbolStep(holder);
  const wide = every < 24;
  const option = useMemo(() => {
    const index = new Map(t.map((time, i) => [time, i]));
    const at = (key: string, time: number) => {
      const i = index.get(time);
      return i === undefined ? null : (s[key][i] ?? null);
    };
    const visible = rain.time.filter((end) => end * 1000 > range[0] && end * 1000 <= range[1]);
    const maxRain = Math.max(0, ...visible.map((end) => rain.precipitation[rain.time.indexOf(end)] ?? 0));
    const symbols = rain.time
      .map((end, i) => ({ end, middle: end - 3 * 3600, i }))
      // Daily symbols use the window ending at 12 UTC (late morning, official time).
      .filter(({ end }) => (end - (every === 24 ? 12 * 3600 : 0)) % (every * 3600) === 0);
    const cloudAt = (time: number) => {
      const values = [at("cloud_cover_low", time), at("cloud_cover_mid", time), at("cloud_cover_high", time)];
      if (values.some((v) => v === null)) return null;
      const [low, mid, high] = values as number[];
      return Math.max(low, mid, high * 0.6);
    };
    const panels: Panel[] = [
      { unit: "", height: 22, bare: true },
      {
        title: "Temperatura",
        unit: "°C",
        height: 210,
        right: { unit: "lluvia mm/6 h", max: Math.max(5, Math.ceil(maxRain * 1.15)) },
      },
      { unit: "", height: 18, bare: true },
      { title: "Viento a 10 m", unit: "km/h", height: 76, min: 0, ticks: 2 },
      { title: "Presión a nivel del mar", unit: "hPa", height: 64, ticks: 2 },
      { title: "Temperatura a 850 hPa", unit: "°C", height: 64, ticks: 2 },
    ];
    return {
      height: panels.reduce((sum, p) => sum + p.height + (p.title ? 44 : 4), 0) + 26 + TOP_DATES,
      option: panelOption(
        panels,
        [
          {
            type: "scatter",
            name: "Cielo",
            panel: 0,
            unit: "",
            legend: false,
            silent: true,
            symbolSize: 0,
            label: { show: true, fontSize: 13, formatter: (p: { data: { sky: string } }) => p.data.sky },
            data: symbols.map(({ middle, i }) => ({
              value: [middle * 1000, 0.5],
              sky: skySymbol(cloudAt(middle), rain.precipitation[i], rain.snowfall[i], middle * 1000),
            })),
          },
          {
            type: "bar",
            name: "Precipitación 6 h",
            panel: 1,
            right: true,
            unit: "mm",
            color: BLUE,
            barMaxWidth: wide ? 16 : 8,
            barMinWidth: 3,
            itemStyle: { color: "rgba(42, 120, 214, 0.78)", borderRadius: [2, 2, 0, 0] },
            labelLayout: { hideOverlap: true },
            label: {
              show: true,
              position: "top",
              fontSize: 9,
              color: "#1f5fae",
              formatter: (p: { value: [number, number | null] }) =>
                p.value[1] !== null && p.value[1] >= (wide ? 0.2 : 1) ? axisNumber(p.value[1]) : "",
            },
            // Each bar is centred on its 6 h window, which ends at the timestamp.
            data: pairs(rain.time, rain.precipitation).map(([x, v]) => [(x as number) - 3 * 3600000, v || null]),
          },
          {
            ...line(RED, 2.2),
            name: "Temperatura 2 m",
            panel: 1,
            unit: "°C",
            smooth: 0.35,
            data: pairs(t, s.temperature_2m),
          },
          {
            ...line(VIOLET, 1.4, "dotted"),
            name: "Punto de rocío",
            panel: 1,
            unit: "°C",
            smooth: 0.35,
            data: pairs(t, s.dew_point_2m),
          },
          {
            type: "scatter",
            name: "Dirección",
            panel: 2,
            unit: "",
            legend: false,
            silent: true,
            color: INK,
            // Arrows point where the wind blows to (meteorological direction + 180°).
            data: symbols
              .map(({ middle }) => [middle, at("wind_direction_10m", middle), at("wind_speed_10m", middle)])
              .filter(([, direction]) => direction !== null)
              .map(([middle, direction, speed]) => ({
                value: [(middle as number) * 1000, 0.5],
                symbol: "path://M5 0 L10 10 L6 8 L6 16 L4 16 L4 8 L0 10 Z",
                symbolSize: [8, 13],
                symbolRotate: -((direction as number) + 180),
                itemStyle: { color: (speed ?? 0) >= 30 ? RED : INK },
              })),
          },
          { ...line(BLUE), name: "Viento medio", panel: 3, unit: "km/h", data: pairs(t, s.wind_speed_10m) },
          { ...line(RED, 1.4, "dashed"), name: "Rachas", panel: 3, unit: "km/h", data: pairs(t, s.wind_gusts_10m) },
          { ...line(VIOLET), name: "Presión", panel: 4, unit: "hPa", legend: false, data: pairs(t, s.pressure_msl) },
          {
            ...line(VIOLET, 1.6),
            name: "850 hPa",
            panel: 5,
            unit: "°C",
            legend: false,
            smooth: 0.35,
            data: pairs(t, s.temperature_850hPa),
          },
        ],
        range,
      ),
    };
  }, [model, range, every]);
  const root = useChart(option.option, option.height);
  return (
    <div ref={holder}>
    <div
      ref={root}
      className="forecast-chart"
      style={{ height: option.height }}
      role="img"
      aria-label={`Meteograma ${model.label}: estado del cielo, temperatura, punto de rocío, precipitación en 6 horas, viento, presión y temperatura a 850 hPa. Los valores están en la tabla de datos.`}
    />
    </div>
  );
}

const CUMULATIVE_DAYS = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];

/** Index into a 365-day climatology from 1 January; 29 February uses 28 February. */
function climateDay(ms: number) {
  const date = new Date(ms);
  const month = date.getUTCMonth();
  return CUMULATIVE_DAYS[month] + Math.min(date.getUTCDate(), month === 1 ? 28 : 31) - 1;
}

export function EnsembleChart({
  ensemble,
  range,
  climate,
}: {
  ensemble: EnsembleForecast;
  range: [number, number];
  climate: Climate | null;
}) {
  const t850 = ensemble.temperature_850hPa;
  const rain = ensemble.precipitation_6h;
  const option = useMemo(() => {
    const shift = (x: number) => x * 1000 - 3 * 3600000;
    const complete = (i: number) => rain.members.every((m) => m[i] !== null);
    const maxRain = rain.time.map((_, i) =>
      complete(i) ? Math.max(...rain.members.map((m) => m[i] as number)) : null,
    );
    const wet = rain.time.map((_, i) =>
      complete(i)
        ? Math.round((100 * rain.members.filter((m) => (m[i] as number) >= 1).length) / rain.members.length)
        : null,
    );
    // Scale to the mean, not to the wettest member, so a 1 mm mean is clearly visible.
    const visibleMeans = rain.time
      .map((t, i) => (t * 1000 > range[0] && t * 1000 <= range[1] ? (rain.mean[i] ?? 0) : 0));
    const cap = Math.max(2, Math.ceil(Math.max(...visibleMeans) * 1.5));
    const panels: Panel[] = [
      { title: "Temperatura a 850 hPa", unit: "°C", height: 190 },
      { title: "Precipitación en 6 h", unit: "mm", height: 110, min: 0, max: cap, ticks: 2 },
      { title: "Miembros con ≥ 1 mm en 6 h", unit: "%", height: 56, min: 0, max: 100, ticks: 2 },
    ];
    return {
      height: panels.reduce((sum, p) => sum + p.height + 44, 0) + 26 + TOP_DATES,
      option: panelOption(
        panels,
        [
          // Members first so the control run and mean draw on top.
          ...t850.members.slice(1).map((values, i) => ({
            ...line(MEMBER, 1),
            name: `Miembro ${i + 1}`,
            panel: 0,
            unit: "°C",
            legend: false,
            silent: true,
            data: pairs(t850.time, values),
          })),
          { ...line(ORANGE, 1.5), name: "Control", panel: 0, unit: "°C", data: pairs(t850.time, t850.members[0]) },
          { ...line(BLUE, 2.5), name: "Media", panel: 0, unit: "°C", data: pairs(t850.time, t850.mean) },
          ...(climate
            ? [
                {
                  ...line(CLIMATE, 1.8, "dashed"),
                  name: `Media ${climate.period.replace("-", "–")}`,
                  panel: 0,
                  unit: "°C",
                  smooth: true,
                  z: 3,
                  data: t850.time.map((t) => [t * 1000, climate.values[climateDay(t * 1000)] ?? null]),
                },
              ]
            : []),
          {
            type: "bar",
            name: "Precipitación media",
            panel: 1,
            unit: "mm",
            color: BLUE,
            barMaxWidth: 14,
            barMinWidth: 3,
            itemStyle: { color: "rgba(42, 120, 214, 0.85)", borderRadius: [2, 2, 0, 0] },
            labelLayout: { hideOverlap: true },
            label: {
              show: true,
              position: "top",
              fontSize: 9,
              color: "#1f5fae",
              formatter: (p: { value: [number, number | null] }) =>
                p.value[1] !== null && p.value[1] >= 0.3 ? axisNumber(p.value[1]) : "",
            },
            data: rain.time.map((t, i) => [shift(t), rain.mean[i] || null]),
          },
          {
            type: "scatter",
            name: "Miembro más lluvioso",
            panel: 1,
            unit: "mm",
            color: VIOLET,
            // Hollow points; above the scale a triangle at the top edge (real value in the tooltip).
            data: rain.time.map((t, i) => {
              const real = maxRain[i];
              const clipped = real !== null && real > cap;
              return {
                // Dry windows draw nothing; the tooltip still reports 0.
                value: [shift(t), real === null || real === 0 ? null : Math.min(real, cap)],
                real,
                symbol: clipped ? "triangle" : "emptyCircle",
                symbolSize: clipped ? 7 : 5,
              };
            }),
          },
          {
            type: "bar",
            name: "Miembros con ≥ 1 mm",
            panel: 2,
            unit: "%",
            legend: false,
            barMaxWidth: 14,
            barMinWidth: 3,
            itemStyle: { color: "rgba(74, 58, 167, 0.55)", borderRadius: [2, 2, 0, 0] },
            data: rain.time.map((t, i) => [shift(t), wet[i] || null]),
          },
        ],
        range,
      ),
    };
  }, [ensemble, range, climate]);
  const root = useChart(option.option, option.height);
  return (
    <div
      ref={root}
      className="forecast-chart"
      style={{ height: option.height }}
      role="img"
      aria-label={`Diagrama de conjunto ${ensemble.label}: ${ensemble.members} miembros de temperatura a 850 hPa, media, control y media climática, y precipitación media en 6 horas con el miembro más lluvioso.`}
    />
  );
}
