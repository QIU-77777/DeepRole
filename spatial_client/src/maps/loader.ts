export type MapId = "campus_center" | "arts_hallway" | "clubroom" | "rooftop";

export type MapNpc = {
  id: string;
  label: string;
  x: number;
  y: number;
  color: string;
  interaction: string;
  kind?: "major" | "ambient";
  bubble?: string;
  waypoint?: string;
};

export type MapExit = {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  target: MapId;
  spawn: { x: number; y: number };
  minutes: number;
  endDay?: boolean;
};

export type MapDefinition = {
  label: string;
  width: number;
  height: number;
  background: string;
  spawn: { x: number; y: number };
  walls: Array<{ x: number; y: number; w: number; h: number }>;
  exits: MapExit[];
  npcs: MapNpc[];
  waypoints: Record<string, { x: number; y: number }>;
};

export type MapCollection = { tileSize: number; maps: Record<MapId, MapDefinition> };

type TiledProperty = { name: string; value: unknown };
type TiledObject = { id: number; name?: string; type?: string; x: number; y: number; width?: number; height?: number; properties?: TiledProperty[] };

function property(object: TiledObject, name: string): unknown {
  return object.properties?.find((item) => item.name === name)?.value;
}

function objectLayer(source: Record<string, unknown>, name: string): TiledObject[] {
  const layers = Array.isArray(source.layers) ? source.layers : [];
  const layer = layers.find((item) => (item as { name?: string }).name === name) as { objects?: TiledObject[] } | undefined;
  return layer?.objects ?? [];
}

function normalizeGraybox(source: Record<string, unknown>): MapCollection | null {
  if (!source.maps || typeof source.maps !== "object") return null;
  return source as unknown as MapCollection;
}

/** Normalize either the current collection or one Tiled map export. */
export function normalizeMapData(source: unknown): MapCollection {
  const value = source as Record<string, unknown>;
  const graybox = normalizeGraybox(value);
  if (graybox) return graybox;
  if (value.type !== "map") throw new Error("Unsupported map JSON format");

  const mapId = String(property({ properties: value.properties as TiledProperty[] } as TiledObject, "map_id") || "campus_center") as MapId;
  const tileSize = Number(value.tilewidth || 32);
  const exits = objectLayer(value, "exits").map((object) => ({
    id: object.name || `exit-${object.id}`,
    x: object.x / tileSize,
    y: object.y / tileSize,
    w: (object.width || tileSize) / tileSize,
    h: (object.height || tileSize) / tileSize,
    target: String(property(object, "target")) as MapId,
    spawn: { x: Number(property(object, "spawn_x") || 0), y: Number(property(object, "spawn_y") || 0) },
    minutes: Number(property(object, "minutes") || 0),
    endDay: Boolean(property(object, "end_day")),
  }));
  const npcs = objectLayer(value, "npcs").map((object) => ({
    id: object.name || `npc-${object.id}`,
    label: String(property(object, "label") || object.name || "角色"),
    x: object.x / tileSize,
    y: object.y / tileSize,
    color: String(property(object, "color") || "#d2ad58"),
    interaction: String(property(object, "interaction") || "主要角色"),
    kind: (String(property(object, "kind") || "major") as "major" | "ambient"),
    bubble: String(property(object, "bubble") || "") || undefined,
    waypoint: String(property(object, "waypoint") || "") || undefined,
  }));
  const waypoints = Object.fromEntries(
    objectLayer(value, "waypoints").map((object) => [
      object.name || `waypoint-${object.id}`,
      { x: object.x / tileSize, y: object.y / tileSize },
    ]),
  );
  return {
    tileSize,
    maps: {
      [mapId]: {
        label: String(property({ properties: value.properties as TiledProperty[] } as TiledObject, "label") || mapId),
        width: Number(value.width || 1),
        height: Number(value.height || 1),
        background: String(property({ properties: value.properties as TiledProperty[] } as TiledObject, "background") || "#789b7e"),
        spawn: { x: Number(property({ properties: value.properties as TiledProperty[] } as TiledObject, "spawn_x") || 1), y: Number(property({ properties: value.properties as TiledProperty[] } as TiledObject, "spawn_y") || 1) },
        walls: objectLayer(value, "walls").map((object) => ({
          x: object.x / tileSize,
          y: object.y / tileSize,
          w: (object.width || tileSize) / tileSize,
          h: (object.height || tileSize) / tileSize,
        })),
        exits,
        npcs,
        waypoints,
      },
    } as Record<MapId, MapDefinition>,
  };
}
