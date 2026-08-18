import type { MapDefinition } from "./loader";

export type GridPoint = { x: number; y: number };

function overlaps(a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }): boolean {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
}

function isBlocked(definition: MapDefinition, point: GridPoint, start: GridPoint, goal: GridPoint): boolean {
  if (point.x < 0 || point.y < 0 || point.x >= definition.width || point.y >= definition.height) return true;
  if ((point.x === start.x && point.y === start.y) || (point.x === goal.x && point.y === goal.y)) return false;
  return definition.walls.some((wall) => overlaps({ x: point.x, y: point.y, w: 1, h: 1 }, wall));
}

/** Find a short 8-direction walk over the map's collision rectangles. */
export function findGridPath(definition: MapDefinition, start: GridPoint, goal: GridPoint): GridPoint[] {
  const source = { x: Math.round(start.x), y: Math.round(start.y) };
  const target = { x: Math.round(goal.x), y: Math.round(goal.y) };
  if (source.x === target.x && source.y === target.y) return [source];

  const directions = [
    { x: -1, y: -1 }, { x: 0, y: -1 }, { x: 1, y: -1 },
    { x: -1, y: 0 },                         { x: 1, y: 0 },
    { x: -1, y: 1 },  { x: 0, y: 1 },  { x: 1, y: 1 },
  ];
  const key = (point: GridPoint) => `${point.x},${point.y}`;
  const queue: GridPoint[] = [source];
  const parents = new Map<string, GridPoint | null>([[key(source), null]]);
  while (queue.length) {
    const current = queue.shift()!;
    for (const direction of directions) {
      const next = { x: current.x + direction.x, y: current.y + direction.y };
      if (isBlocked(definition, next, source, target) || parents.has(key(next))) continue;
      if (direction.x !== 0 && direction.y !== 0) {
        const horizontal = { x: current.x + direction.x, y: current.y };
        const vertical = { x: current.x, y: current.y + direction.y };
        if (isBlocked(definition, horizontal, source, target) || isBlocked(definition, vertical, source, target)) continue;
      }
      parents.set(key(next), current);
      if (next.x === target.x && next.y === target.y) {
        const path: GridPoint[] = [target];
        let cursor: GridPoint | null = current;
        while (cursor) {
          path.push(cursor);
          cursor = parents.get(key(cursor)) ?? null;
        }
        return path.reverse();
      }
      queue.push(next);
    }
  }
  // A hand-authored waypoint should normally be reachable. Keeping the
  // destination visible is safer than silently leaving an NPC at stale data
  // if an author later places a waypoint inside a new collision rectangle.
  return [source, target];
}
