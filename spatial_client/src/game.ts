import Phaser from "phaser";
import mapData from "./maps/graybox.json";

export type MapId = "campus_center" | "arts_hallway" | "clubroom" | "rooftop";

export interface SpatialGameState {
  mapId: MapId;
  player: { x: number; y: number };
  nearbyNpc: { id: string; label: string; interaction: string } | null;
  nearbyExit: { id: string; target: MapId; endDay?: boolean } | null;
}

type Hooks = {
  onState: (state: SpatialGameState) => void;
  onTransition?: (request: { fromMap: MapId; exitId: string }) => Promise<{ mapId: MapId; x: number; y: number } | null>;
  isInputLocked?: () => boolean;
  npcLocations?: Record<string, MapId>;
};

type MapNpc = { id: string; label: string; x: number; y: number; color: string; interaction: string };
type MapExit = { id: string; x: number; y: number; w: number; h: number; target: MapId; spawn: { x: number; y: number }; minutes: number; endDay?: boolean };
type MapDefinition = { label: string; width: number; height: number; background: string; spawn: { x: number; y: number }; walls: Array<{ x: number; y: number; w: number; h: number }>; exits: MapExit[]; npcs: MapNpc[] };
const maps = mapData.maps as Record<MapId, MapDefinition>;

const PLAYER_SPEED = 150;
const INTERACTION_DISTANCE = 62;

class SpatialScene extends Phaser.Scene {
  private mapId: MapId = "campus_center";
  private player!: Phaser.GameObjects.Rectangle;
  private walls!: Phaser.Physics.Arcade.StaticGroup;
  private cursors!: Phaser.Types.Input.Keyboard.CursorKeys;
  private keys!: Record<string, Phaser.Input.Keyboard.Key>;
  private hooks!: Hooks;
  private npcs: Array<{ data: MapNpc; body: Phaser.GameObjects.Rectangle }> = [];
  private exits: Array<{ data: MapExit; zone: Phaser.GameObjects.Zone }> = [];
  private lastPublished = "";
  private nameLabels: Phaser.GameObjects.Text[] = [];
  private transitioning = false;
  private npcLocations: Record<string, MapId> = {};

  constructor() {
    super("spatial-scene");
  }

  create(data: { hooks: Hooks; mapId?: MapId; spawn?: { x: number; y: number } }) {
    this.hooks = data.hooks;
    this.npcLocations = data.hooks.npcLocations ?? {};
    this.cursors = this.input.keyboard!.createCursorKeys();
    this.keys = this.input.keyboard!.addKeys("W,A,S,D,E") as Record<string, Phaser.Input.Keyboard.Key>;
    this.loadMap(data.mapId ?? "campus_center", data.spawn);
  }

  update() {
    if (!this.player || !this.cursors) return;
    if (this.hooks.isInputLocked?.()) {
      this.player.setVelocity(0, 0);
      return;
    }
    const left = this.cursors.left.isDown || this.keys.A.isDown;
    const right = this.cursors.right.isDown || this.keys.D.isDown;
    const up = this.cursors.up.isDown || this.keys.W.isDown;
    const down = this.cursors.down.isDown || this.keys.S.isDown;
    const vector = new Phaser.Math.Vector2(Number(right) - Number(left), Number(down) - Number(up));
    if (vector.lengthSq() > 0) vector.normalize().scale(PLAYER_SPEED);
    this.player.setVelocity(vector.x, vector.y);
    this.publishState();
  }

  public interact() {
    const state = this.currentState();
    if (state.nearbyNpc) this.hooks.onState(state);
    if (state.nearbyExit?.endDay) this.hooks.onState(state);
  }

  public switchMap(mapId: MapId, spawn?: { x: number; y: number }) {
    this.loadMap(mapId, spawn);
  }

  public updateNpcLocations(locations: Record<string, MapId>) {
    this.npcLocations = locations;
    this.loadMap(this.mapId, {
      x: this.player.x / mapData.tileSize - 0.5,
      y: this.player.y / mapData.tileSize - 0.5,
    });
  }

  private loadMap(mapId: MapId, spawn?: { x: number; y: number }) {
    this.mapId = mapId;
    this.children.removeAll(true);
    this.npcs = [];
    this.exits = [];
    this.nameLabels = [];
    const definition = maps[mapId];
    const width = definition.width * mapData.tileSize;
    const height = definition.height * mapData.tileSize;
    this.physics.world.setBounds(0, 0, width, height);
    this.cameras.main.setBounds(0, 0, width, height);
    this.drawGrid(definition);
    this.walls = this.physics.add.staticGroup();
    for (const wall of definition.walls) {
      const body = this.add.rectangle((wall.x + wall.w / 2) * mapData.tileSize, (wall.y + wall.h / 2) * mapData.tileSize, wall.w * mapData.tileSize, wall.h * mapData.tileSize, 0x24332d, 0.9);
      this.physics.add.existing(body, true);
      this.walls.add(body);
    }
    const initial = spawn ?? definition.spawn;
    this.player = this.add.rectangle((initial.x + 0.5) * mapData.tileSize, (initial.y + 0.5) * mapData.tileSize, 22, 28, 0xe9e0bd);
    this.physics.add.existing(this.player);
    this.player.setDepth(10);
    this.physics.add.collider(this.player, this.walls);
    this.cameras.main.startFollow(this.player, true, 0.08, 0.08);
    this.cameras.main.setDeadzone(160, 96);
    for (const npc of definition.npcs) {
      const location = this.npcLocations[npc.id];
      if (!location || location === mapId) this.addNpc(npc);
    }
    for (const exit of definition.exits) this.addExit(exit);
    this.publishState(true);
  }

  private drawGrid(definition: MapDefinition) {
    const graphics = this.add.graphics();
    graphics.fillStyle(Number(definition.background.replace("#", "0x")), 1);
    graphics.fillRect(0, 0, definition.width * mapData.tileSize, definition.height * mapData.tileSize);
    graphics.lineStyle(1, 0xffffff, 0.06);
    for (let x = 0; x <= definition.width; x++) graphics.lineBetween(x * mapData.tileSize, 0, x * mapData.tileSize, definition.height * mapData.tileSize);
    for (let y = 0; y <= definition.height; y++) graphics.lineBetween(0, y * mapData.tileSize, definition.width * mapData.tileSize, y * mapData.tileSize);
  }

  private addNpc(npc: MapNpc) {
    const body = this.add.rectangle((npc.x + 0.5) * mapData.tileSize, (npc.y + 0.5) * mapData.tileSize, 24, 30, Number(npc.color.replace("#", "0x")));
    body.setDepth(9);
    const label = this.add.text(body.x, body.y - 28, npc.label, { color: "#fff8e5", fontFamily: "sans-serif", fontSize: "13px", backgroundColor: "#17231fcc", padding: { x: 4, y: 2 } }).setOrigin(0.5, 1).setDepth(20);
    this.npcs.push({ data: npc, body });
    this.nameLabels.push(label);
  }

  private addExit(exit: MapExit) {
    const zone = this.add.zone((exit.x + exit.w / 2) * mapData.tileSize, (exit.y + exit.h / 2) * mapData.tileSize, exit.w * mapData.tileSize, exit.h * mapData.tileSize);
    this.physics.add.existing(zone, true);
    this.physics.add.overlap(this.player, zone, async () => {
      if (this.transitioning || exit.endDay) return;
      this.transitioning = true;
      const serverState = await this.hooks.onTransition?.({ fromMap: this.mapId, exitId: exit.id });
      if (serverState) {
        this.switchMap(serverState.mapId, {
          x: serverState.x / mapData.tileSize - 0.5,
          y: serverState.y / mapData.tileSize - 0.5,
        });
      } else {
        this.switchMap(exit.target as MapId, exit.spawn);
      }
      this.transitioning = false;
      this.hooks.onState(this.currentState());
    });
    this.exits.push({ data: exit, zone });
  }

  private currentState(): SpatialGameState {
    const nearestNpc = this.npcs
      .map(({ data, body }) => ({ data, distance: Phaser.Math.Distance.Between(this.player.x, this.player.y, body.x, body.y) }))
      .filter(({ distance }) => distance <= INTERACTION_DISTANCE)
      .sort((a, b) => a.distance - b.distance)[0]?.data;
    const nearestExit = this.exits
      .map(({ data, zone }) => ({ data, distance: Phaser.Math.Distance.Between(this.player.x, this.player.y, zone.x, zone.y) }))
      .filter(({ distance }) => distance <= INTERACTION_DISTANCE)
      .sort((a, b) => a.distance - b.distance)[0]?.data;
    return {
      mapId: this.mapId,
      player: { x: Math.round(this.player.x), y: Math.round(this.player.y) },
      nearbyNpc: nearestNpc ? { id: nearestNpc.id, label: nearestNpc.label, interaction: nearestNpc.interaction } : null,
      nearbyExit: nearestExit ? { id: nearestExit.id, target: nearestExit.target as MapId, endDay: nearestExit.endDay } : null,
    };
  }

  private publishState(force = false) {
    const state = this.currentState();
    const serial = JSON.stringify(state);
    if (!force && serial === this.lastPublished) return;
    this.lastPublished = serial;
    this.hooks.onState(state);
  }
}

export function createSpatialGame(parent: HTMLElement, hooks: Hooks) {
  const game = new Phaser.Game({
    type: Phaser.AUTO,
    parent,
    width: 960,
    height: 600,
    backgroundColor: "#17231f",
    pixelArt: true,
    physics: { default: "arcade", arcade: { debug: false } },
    scene: SpatialScene,
    scale: { mode: Phaser.Scale.RESIZE, autoCenter: Phaser.Scale.CENTER_BOTH },
  });
  game.scene.start("spatial-scene", { hooks });
  return game;
}
