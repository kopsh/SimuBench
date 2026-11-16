/** Where "add" puts a new node so that it covers none of the bench's nodes (#1258). */

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function intersects(a: Rect, b: Rect, gap = 0): boolean {
  return a.x < b.x + b.width + gap && b.x < a.x + a.width + gap && a.y < b.y + b.height + gap && b.y < a.y + a.height + gap;
}

/**
 * The top-left corner for a new node of `size`: the `preferred` spot (the middle of the view) when it
 * is free, else the first free cell of a grid around it, nearest first, else the right of everything.
 * Free means `gap` away from every existing node, so the node and its port labels stand apart.
 */
export function freeSpot(nodes: readonly Rect[], size: { width: number; height: number }, preferred: { x: number; y: number }, gap = 40) {
  const free = (p: { x: number; y: number }) => !nodes.some((n) => intersects({ ...p, ...size }, n, gap));
  if (free(preferred)) return { x: Math.round(preferred.x), y: Math.round(preferred.y) };
  if (nodes.length === 0) return { x: Math.round(preferred.x), y: Math.round(preferred.y) };
  const left = Math.min(...nodes.map((n) => n.x));
  const top = Math.min(...nodes.map((n) => n.y));
  const right = Math.max(...nodes.map((n) => n.x + n.width));
  const bottom = Math.max(...nodes.map((n) => n.y + n.height));
  // Cells over the nodes' bounding box and one node beyond it on every side.
  const stepX = size.width + gap;
  const stepY = size.height + gap;
  const cells: { x: number; y: number }[] = [];
  for (let y = top - stepY; y <= bottom + gap; y += stepY / 2) {
    for (let x = left - stepX; x <= right + gap; x += stepX / 2) cells.push({ x, y });
  }
  const distance = (p: { x: number; y: number }) => Math.hypot(p.x - preferred.x, p.y - preferred.y);
  const best = cells.filter(free).sort((a, b) => distance(a) - distance(b))[0] ?? { x: right + gap, y: top };
  return { x: Math.round(best.x), y: Math.round(best.y) };
}
