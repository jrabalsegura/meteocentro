import { useEffect, useMemo, useRef } from "react";
import { init, use, type EChartsCoreOption } from "echarts/core";
import { LineChart, BarChart } from "echarts/charts";
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
import type { EnsembleForecast, ModelForecast } from "./ForecastPage";

use([
  LineChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  AxisPointerComponent,
  MarkLineComponent,
  TitleComponent,
  AriaComponent,
  SVGRenderer,
]);

// Validated categorical order (CVD-safe on white): orange, blue, violet.
const ORANGE = "#eb6834";
const BLUE = "#2a78d6";
const VIOLET = "#4a3aa7";
const MEMBER = "rgba(98, 110, 128, 0.28)";
const INK = "#5c6b7e";
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

type Panel = { title: string; unit: string; height: number; min?: number; max?: number };

function pairs(time: number[], values: (number | null)[]) {
  return time.map((t, i) => [t * 1000, values[i] ?? null]);
}

function fmt(value: unknown, unit: string) {
  return typeof value === "number"
    ? `${value.toLocaleString("es-ES", { maximumFractionDigits: 1 })} ${unit}`
    : "sin dato";
}

/** Stacked panels, one y-axis each, sharing a single time axis and crosshair. */
function panelOption(
  panels: Panel[],
  series: (Record<string, unknown> & { panel: number; unit: string })[],
  range: [number, number],
): EChartsCoreOption {
  const top = 44;
  const gap = 52;
  let y = top;
  const grids = panels.map((panel) => {
    const grid = { left: 48, right: 14, top: y, height: panel.height };
    y += panel.height + gap;
    return grid;
  });
  const now = Date.now();
  return {
    animation: false,
    aria: { enabled: true },
    textStyle: { fontFamily: "Inter, ui-sans-serif, sans-serif" },
    title: panels.map((panel, i) => ({
      text: `${panel.title} (${panel.unit})`,
      top: grids[i].top - 42,
      left: 48,
      textStyle: { fontSize: 11, fontWeight: 600, color: INK },
    })),
    // One small legend per panel, on its own row under the panel title.
    legend: panels
      .map((_, i) => ({
        top: grids[i].top - 24,
        left: 44,
        right: 8,
        itemWidth: 14,
        itemHeight: 8,
        itemGap: 12,
        textStyle: { fontSize: 11, color: INK },
        data: series
          .filter((s) => s.panel === i && s.legend !== false)
          .map((s) => s.name as string),
      }))
      .filter((legend) => legend.data.length > 1),
    grid: grids,
    axisPointer: { link: [{ xAxisIndex: "all" }], label: { show: false } },
    tooltip: {
      trigger: "axis",
      confine: true,
      renderMode: "richText",
      formatter: (items: { axisValue: number; seriesName: string; value: [number, number | null]; seriesIndex: number }[]) => {
        if (!items.length) return "";
        const lines = items
          .filter((item) => series[item.seriesIndex]?.legend !== false)
          .map((item) => `${item.seriesName}: ${fmt(item.value[1], series[item.seriesIndex].unit)}`);
        return [hourLabel.format(items[0].axisValue), ...lines].join("\n");
      },
    },
    xAxis: panels.map((_, i) => ({
      type: "time",
      gridIndex: i,
      min: range[0],
      max: range[1],
      axisLine: { lineStyle: { color: "#c4ccd8" } },
      axisTick: { show: false },
      splitLine: { show: true, lineStyle: { color: GRID_LINE } },
      splitNumber: 8,
      axisLabel: {
        show: i === panels.length - 1,
        color: INK,
        fontSize: 10,
        hideOverlap: true,
        formatter: (value: number) => dayLabel.format(value),
      },
    })),
    yAxis: panels.map((panel, i) => ({
      type: "value",
      gridIndex: i,
      scale: panel.min === undefined,
      min: panel.min,
      max: panel.max,
      splitNumber: 3,
      axisLabel: {
        color: INK,
        fontSize: 10,
        formatter: (value: number) => value.toLocaleString("es-ES", { maximumFractionDigits: 1 }),
      },
      splitLine: { lineStyle: { color: GRID_LINE } },
    })),
    series: series.map(({ panel, unit: _unit, legend: _legend, ...rest }, index) => ({
      xAxisIndex: panel,
      yAxisIndex: panel,
      connectNulls: false,
      showSymbol: false,
      symbolSize: 8,
      smooth: false,
      emphasis: { disabled: true },
      ...rest,
      // A dashed "now" reference in every panel, drawn once per panel.
      markLine:
        series.findIndex((s) => s.panel === panel) === index && now > range[0] && now < range[1]
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

function line(color: string, width = 2, dashed = false) {
  return { type: "line", color, lineStyle: { color, width, type: dashed ? "dashed" : "solid" } };
}

export function Meteogram({ model, range }: { model: ModelForecast; range: [number, number] }) {
  const s = model.series;
  const t = model.time;
  const rain = model.accumulated_6h;
  const panels: Panel[] = [
    { title: "Temperatura", unit: "°C", height: 110 },
    { title: "Precipitación en 6 h", unit: "mm", height: 70, min: 0 },
    { title: "Presión a nivel del mar", unit: "hPa", height: 70 },
    { title: "Viento a 10 m", unit: "km/h", height: 70, min: 0 },
    { title: "Nubosidad", unit: "%", height: 70, min: 0, max: 100 },
  ];
  const height = 44 + panels.reduce((sum, p) => sum + p.height + 52, 0);
  const option = useMemo(() => panelOption(
    panels,
    [
      { ...line(ORANGE), name: "Temperatura 2 m", panel: 0, unit: "°C", data: pairs(t, s.temperature_2m) },
      { ...line(BLUE, 1.5), name: "Punto de rocío", panel: 0, unit: "°C", data: pairs(t, s.dew_point_2m) },
      { ...line(VIOLET, 2, true), name: "850 hPa", panel: 0, unit: "°C", data: pairs(t, s.temperature_850hPa) },
      {
        type: "bar",
        name: "Precipitación",
        panel: 1,
        unit: "mm",
        color: BLUE,
        barMaxWidth: 8,
        itemStyle: { borderRadius: [2, 2, 0, 0] },
        // Bars centred on each 6 h window, which ends at the timestamp.
        data: pairs(rain.time, rain.precipitation).map(([x, v]) => [(x as number) - 3 * 3600000, v]),
      },
      {
        type: "bar",
        name: "Nieve (cm)",
        panel: 1,
        unit: "cm",
        color: VIOLET,
        barMaxWidth: 8,
        itemStyle: { borderRadius: [2, 2, 0, 0] },
        data: pairs(rain.time, rain.snowfall).map(([x, v]) => [(x as number) - 3 * 3600000, v || null]),
      },
      { ...line(VIOLET), name: "Presión", panel: 2, unit: "hPa", legend: false, data: pairs(t, s.pressure_msl) },
      { ...line(BLUE), name: "Viento medio", panel: 3, unit: "km/h", data: pairs(t, s.wind_speed_10m) },
      { ...line(ORANGE, 1.5, true), name: "Rachas", panel: 3, unit: "km/h", data: pairs(t, s.wind_gusts_10m) },
      { ...line(BLUE, 1), name: "Nubes bajas", panel: 4, unit: "%", data: pairs(t, s.cloud_cover_low) },
      { ...line(VIOLET, 1), name: "Nubes medias", panel: 4, unit: "%", data: pairs(t, s.cloud_cover_mid) },
      { ...line(ORANGE, 1, true), name: "Nubes altas", panel: 4, unit: "%", data: pairs(t, s.cloud_cover_high) },
    ],
    range,
  ), [model, range]);
  const root = useChart(option, height);
  return (
    <div
      ref={root}
      className="forecast-chart"
      style={{ height }}
      role="img"
      aria-label={`Meteograma ${model.label}: temperatura, precipitación, presión, viento y nubosidad. Los valores están en la tabla de datos.`}
    />
  );
}

export function EnsembleChart({ ensemble, range }: { ensemble: EnsembleForecast; range: [number, number] }) {
  const t850 = ensemble.temperature_850hPa;
  const rain = ensemble.precipitation_6h;
  const panels: Panel[] = [
    { title: "Temperatura a 850 hPa", unit: "°C", height: 190 },
    { title: "Precipitación en 6 h", unit: "mm", height: 90, min: 0 },
  ];
  const height = 44 + panels.reduce((sum, p) => sum + p.height + 52, 0);
  const maxRain = rain.time.map((_, i) => {
    const values = rain.members.map((m) => m[i]);
    return values.some((v) => v === null) ? null : Math.max(...(values as number[]));
  });
  const shift = (points: (number | null)[][]) =>
    points.map(([x, v]) => [(x as number) - 3 * 3600000, v]);
  const option = useMemo(() => panelOption(
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
      {
        type: "bar",
        name: "Precipitación media",
        panel: 1,
        unit: "mm",
        color: BLUE,
        barMaxWidth: 8,
        itemStyle: { borderRadius: [2, 2, 0, 0] },
        data: shift(pairs(rain.time, rain.mean)),
      },
      {
        ...line(VIOLET, 1.5, true),
        name: "Máximo de los miembros",
        panel: 1,
        unit: "mm",
        showSymbol: true,
        symbol: "circle",
        symbolSize: 4,
        data: shift(pairs(rain.time, maxRain)),
      },
    ],
    range,
  ), [ensemble, range]);
  const root = useChart(option, height);
  return (
    <div
      ref={root}
      className="forecast-chart"
      style={{ height }}
      role="img"
      aria-label={`Diagrama de conjunto ${ensemble.label}: ${ensemble.members} miembros de temperatura a 850 hPa, media y control, y precipitación media en 6 horas.`}
    />
  );
}
