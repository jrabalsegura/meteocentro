import { useEffect, useRef } from "react";
import { init, use } from "echarts/core";
import { LineChart, BarChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  AriaComponent,
} from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import type { HistoricalPoint } from "./HistoryPage";
import { date, historyMethod } from "./data";
use([
  LineChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  AriaComponent,
  SVGRenderer,
]);
export default function HistoryChart({
  items,
  metric,
  range,
}: {
  items: HistoricalPoint[];
  metric: string;
  range: [string, string];
}) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!root.current) return;
    const chart = init(root.current, undefined, { renderer: "svg" });
    const groups = new Map<string, HistoricalPoint[]>();
    items.forEach((item) => {
      if (!groups.has(item.channel)) groups.set(item.channel, []);
      groups.get(item.channel)!.push(item);
    });
    const series = [];
    let index = 0;
    for (const points of groups.values()) {
      const first = points[0];
      const prefix = `${historyMethod(first.aggregation_method)} · ${++index}`;
      for (const field of first.aggregation_method === "source_observation"
        ? ["value"]
        : metric.startsWith("rain")
          ? ["total"]
          : ["mean", "minimum", "maximum"]) {
        const data: [number, number | null][] = [];
        let previousTime: number | null = null;
        for (const point of points) {
          const t = Date.parse(point.time ?? point.period_start!);
          if (
            (point.break_before ||
              (previousTime !== null && t - previousTime > 90000000)) &&
            data.length
          )
            data.push([t - 1, null]);
          data.push([
            t,
            (point[field as keyof HistoricalPoint] as number | null) ?? null,
          ]);
          previousTime = t;
        }
        series.push({
          name: `${prefix} · ${{ value: "valor", total: "total", mean: "media", minimum: "mínima", maximum: "máxima" }[field]}`,
          type:
            metric.startsWith("rain") || first.aggregation_method === "provider"
              ? "bar"
              : "line",
          data,
          connectNulls: false,
          smooth: false,
          showSymbol: true,
          symbolSize: 4,
          animation: false,
          lineStyle: {
            width: field === "mean" || field === "value" ? 2 : 1,
            type: field === "mean" || field === "value" ? "solid" : "dashed",
          },
          emphasis: { focus: "series" },
        });
      }
    }
    chart.setOption({
      animation: false,
      aria: { enabled: true },
      color: ["#16688a", "#5144a0", "#c65e20", "#397547"],
      grid: { left: 56, right: 22, top: 24, bottom: 106 },
      tooltip: { trigger: "axis", renderMode: "richText" },
      xAxis: {
        type: "time",
        min: Date.parse(range[0]),
        max: Date.parse(range[1]),
        axisLabel: {
          formatter: (v: number) => date(new Date(v).toISOString()),
          hideOverlap: true,
        },
      },
      yAxis: { type: "value", scale: true, name: items[0]?.unit ?? "" },
      legend: { type: "scroll", bottom: 0, textStyle: { fontSize: 10 } },
      dataZoom: [
        { type: "inside" },
        { type: "slider", bottom: 40, height: 20 },
      ],
      series,
    });
    const resize = new ResizeObserver(() => chart.resize());
    resize.observe(root.current);
    return () => {
      resize.disconnect();
      chart.dispose();
    };
  }, [items, metric, range]);
  return (
    <div
      ref={root}
      className="history-chart"
      role="img"
      aria-label="Gráfico histórico con huecos visibles. Los valores están disponibles en la tabla."
    />
  );
}
