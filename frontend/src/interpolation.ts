// Visual colour field interpolated between station readings (inverse distance
// weighting). It is a presentation aid only: nothing is stored, and areas far
// from any station stay transparent instead of inventing values.

export type FieldPoint = { lng: number; lat: number; value: number };
export type Ramp = { stops: [number, string, number?][]; label: string };
export type Extent = { west: number; south: number; east: number; north: number };

// Colour ramps for the field (lighter than the marker text colours). The third
// element is an optional opacity, so that «no rain» stays transparent.
export const fieldRamps: Record<string, Ramp> = {
  temperature: {
    label: "°C",
    stops: [
      [-10, "#6a00a8"],
      [-5, "#3b3fd6"],
      [0, "#1f78d1"],
      [5, "#1aaec8"],
      [10, "#22b36b"],
      [15, "#9fd431"],
      [20, "#f2d133"],
      [25, "#f59a2b"],
      [30, "#e8542a"],
      [35, "#c81e3a"],
      [40, "#7d0a4f"],
    ],
  },
  humidity: {
    label: "%",
    stops: [
      [10, "#a6611a"],
      [30, "#dfc27d"],
      [50, "#f5f5f5"],
      [70, "#80cdc1"],
      [90, "#2c8a80"],
      [100, "#01665e"],
    ],
  },
  rain_today: {
    label: "mm",
    stops: [
      [0, "#c6dbef", 0],
      [0.1, "#c6dbef", 0.35],
      [1, "#6baed6"],
      [5, "#2171b5"],
      [15, "#6a51a3"],
      [30, "#ae017e"],
      [60, "#67001f"],
    ],
  },
};
fieldRamps.rain = fieldRamps.rain_today;

export const fieldMetrics = Object.keys(fieldRamps);

function hex(color: string) {
  const n = parseInt(color.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function rampColor(ramp: Ramp, value: number): [number, number, number, number] {
  const stops = ramp.stops;
  if (value <= stops[0][0]) return [...hex(stops[0][1]), stops[0][2] ?? 1] as never;
  for (let i = 1; i < stops.length; i++) {
    const [v1, c1, a1 = 1] = stops[i];
    if (value <= v1) {
      const [v0, c0, a0 = 1] = stops[i - 1];
      const t = (value - v0) / (v1 - v0);
      const p = hex(c0), q = hex(c1);
      return [
        p[0] + (q[0] - p[0]) * t,
        p[1] + (q[1] - p[1]) * t,
        p[2] + (q[2] - p[2]) * t,
        a0 + (a1 - a0) * t,
      ];
    }
  }
  const last = stops[stops.length - 1];
  return [...hex(last[1]), last[2] ?? 1] as never;
}

export function rampGradient(ramp: Ramp) {
  const min = ramp.stops[0][0], max = ramp.stops[ramp.stops.length - 1][0];
  return `linear-gradient(to right, ${ramp.stops
    .map(([v, c, a = 1]) => {
      const [r, g, b] = hex(c);
      return `rgba(${r},${g},${b},${Math.max(a, 0.15)}) ${((v - min) / (max - min)) * 100}%`;
    })
    .join(", ")})`;
}

// Distances in km with an equirectangular approximation: enough at this scale.
const KM_LAT = 110.57;
export const SEARCH_KM = 30; // stations further away do not influence a cell
export const SOLID_KM = 10; // full opacity when a station is this close
export const FADE_KM = 25; // transparent beyond this distance to the nearest station

const mercY = (lat: number) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
const latFromMercY = (y: number) => (360 / Math.PI) * Math.atan(Math.exp(y)) - 90;

/**
 * Rasterise the field into RGBA pixels covering `extent`. Rows are spaced in
 * Web Mercator so the image lines up with MapLibre's image source.
 */
export function renderField(
  points: FieldPoint[],
  ramp: Ramp,
  extent: Extent,
  width: number,
  height: number,
  opacity = 1,
): Uint8ClampedArray<ArrayBuffer> {
  const pixels = new Uint8ClampedArray(width * height * 4);
  if (!points.length) return pixels;
  const midLat = (extent.north + extent.south) / 2;
  const kmLng = 111.32 * Math.cos((midLat * Math.PI) / 180);
  // Bucket stations in a coarse grid so each cell only looks at neighbours.
  const cellLng = SEARCH_KM / kmLng, cellLat = SEARCH_KM / KM_LAT;
  const buckets = new Map<string, FieldPoint[]>();
  for (const p of points) {
    const key = `${Math.floor(p.lng / cellLng)}:${Math.floor(p.lat / cellLat)}`;
    const list = buckets.get(key);
    if (list) list.push(p);
    else buckets.set(key, [p]);
  }
  const top = mercY(extent.north), bottom = mercY(extent.south);
  for (let row = 0; row < height; row++) {
    const lat = latFromMercY(top + ((row + 0.5) / height) * (bottom - top));
    const by = Math.floor(lat / cellLat);
    for (let col = 0; col < width; col++) {
      const lng = extent.west + ((col + 0.5) / width) * (extent.east - extent.west);
      const bx = Math.floor(lng / cellLng);
      let weights = 0, sum = 0, nearest = Infinity;
      for (let dx = -1; dx <= 1; dx++)
        for (let dy = -1; dy <= 1; dy++) {
          const list = buckets.get(`${bx + dx}:${by + dy}`);
          if (!list) continue;
          for (const p of list) {
            const ex = (p.lng - lng) * kmLng, ey = (p.lat - lat) * KM_LAT;
            const d = Math.sqrt(ex * ex + ey * ey);
            if (d >= SEARCH_KM) continue;
            if (d < nearest) nearest = d;
            // IDW (power 2) tapered to zero at the search radius so stations
            // entering or leaving the neighbourhood do not create seams.
            const taper = 1 - d / SEARCH_KM;
            const w = (taper * taper) / Math.max(d * d, 0.25);
            weights += w;
            sum += w * p.value;
          }
        }
      if (!weights || nearest >= FADE_KM) continue;
      const fade = nearest <= SOLID_KM ? 1 : 1 - (nearest - SOLID_KM) / (FADE_KM - SOLID_KM);
      const [r, g, b, a] = rampColor(ramp, sum / weights);
      const i = (row * width + col) * 4;
      pixels[i] = r;
      pixels[i + 1] = g;
      pixels[i + 2] = b;
      pixels[i + 3] = 255 * a * fade * opacity;
    }
  }
  return pixels;
}
