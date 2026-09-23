import type { Station } from "./data";

export type ProjectedStation = { station: Station; x: number; y: number };
export type MarkerGroup = { x: number; y: number; items: ProjectedStation[] };

const overlaps = (a: { x: number; y: number }, b: { x: number; y: number }) =>
  Math.abs(a.x - b.x) < 72 && Math.abs(a.y - b.y) < 44;

// Pixel distances control legibility; they have no bearing on physical identity.
export function layoutMarkers(points: ProjectedStation[], width: number, height: number): MarkerGroup[] {
  const groups: MarkerGroup[] = [];
  const grid = new Map<string, number[]>();
  // Keep positions stable when selecting a station or refreshing the API.
  for (const point of [...points].sort((a, b) => a.station.id.localeCompare(b.station.id))) {
    if (point.x < -40 || point.y < -30 || point.x > width + 40 || point.y > height + 30) continue;
    const col = Math.floor(point.x / 72), row = Math.floor(point.y / 44);
    let neighbor: number | undefined;
    for (let x = col - 1; x <= col + 1; x++)
      for (let y = row - 1; y <= row + 1; y++)
        for (const index of grid.get(`${x}:${y}`) ?? [])
          if (overlaps(groups[index], point)) neighbor = index;
    if (neighbor !== undefined) groups[neighbor].items.push(point);
    else {
      const key = `${col}:${row}`;
      const cell = grid.get(key) ?? [];
      cell.push(groups.length);
      grid.set(key, cell);
      groups.push({ x: point.x, y: point.y, items: [point] });
    }
  }

  // Reserve every other marker/group before attempting to spread a group. A
  // later expansion must also respect the individual markers already placed.
  const placements = groups.map((group) => [group]);
  groups.forEach((group, index) => {
    const count = group.items.length;
    if (count === 1) return;
    const center = {
      x: group.items.reduce((sum, item) => sum + item.x, 0) / count,
      y: group.items.reduce((sum, item) => sum + item.y, 0) / count,
    };
    // Try both rows and columns: neighbors on the sides may leave room above.
    for (const columns of new Set([Math.ceil(Math.sqrt(count)), 1, count])) {
      const rows = Math.ceil(count / columns);
      // Avoid moving values far from their published locations to force a fit.
      if ((columns - 1) * 72 > 192 || (rows - 1) * 44 > 192) continue;
      const spread = group.items.map((item, i) => {
        const row = Math.floor(i / columns);
        const rowSize = Math.min(columns, count - row * columns);
        return {
          x: center.x + (i % columns - (rowSize - 1) / 2) * 72,
          y: center.y + (row - (rows - 1) / 2) * 44,
          items: [item],
        };
      });
      if (spread.some((point) =>
        point.x < 40 || point.x > width - 40 || point.y < 80 || point.y > height - 30 ||
        Math.hypot(point.x - point.items[0].x, point.y - point.items[0].y) > 96 ||
        placements.some((others, otherIndex) => otherIndex !== index && others.some((other) => overlaps(point, other)))
      )) continue;
      placements[index] = spread;
      break;
    }
  });
  return placements.flat();
}
