import Phaser from "phaser";
import clubroomBackground from "./assets/clubroom-background.svg";
import linxiActor from "./assets/linxi-actor.svg";
import playerActor from "./assets/player-actor.svg";
import shenzhiyiActor from "./assets/shenzhiyi-actor.svg";
import campusCenterMap from "./maps/tiled/campus_center.json";
import artsHallwayMap from "./maps/tiled/arts_hallway.json";
import clubroomMap from "./maps/tiled/clubroom.json";
import rooftopMap from "./maps/tiled/rooftop.json";
import { mergeMapData, type MapDefinition, type MapExit, type MapNpc, type MapId as LoaderMapId } from "./maps/loader";
import { findGridPath, type GridPoint } from "./maps/grid_path";

export type MapId = LoaderMapId;

export interface SpatialGameState {
  mapId: MapId;
  player: { x: number; y: number };
  nearbyNpc: { id: string; label: string; interaction: string; kind: "major" | "ambient"; bubble?: string } | null;
  nearbyExit: { id: string; target: MapId; endDay?: boolean } | null;
}

type Hooks = {
  onState: (state: SpatialGameState) => void;
  onTransition?: (request: { fromMap: MapId; exitId: string }) => Promise<{
    mapId: MapId;
    x: number;
    y: number;
    npcLocations?: Record<string, MapId>;
    npcWaypoints?: Record<string, string>;
  } | null>;
  isInputLocked?: () => boolean;
  mapId?: MapId;
  initialPlayer?: { x: number; y: number };
  npcLocations?: Record<string, MapId>;
  npcWaypoints?: Record<string, string>;
};

type PhysicsRectangle = Phaser.GameObjects.Rectangle & { body: Phaser.Physics.Arcade.Body };
const mapData = mergeMapData([campusCenterMap, artsHallwayMap, clubroomMap, rooftopMap]);
const maps = mapData.maps;

const PLAYER_SPEED = 150;
const INTERACTION_DISTANCE = 62;
type FacingDirection = "north" | "north-east" | "east" | "south-east" | "south" | "south-west" | "west" | "north-west";

class SpatialScene extends Phaser.Scene {
  private mapId: MapId = "campus_center";
  private player!: PhysicsRectangle;
  private walls!: Phaser.Physics.Arcade.StaticGroup;
  private cursors!: Phaser.Types.Input.Keyboard.CursorKeys;
  private keys!: Record<string, Phaser.Input.Keyboard.Key>;
  private hooks!: Hooks;
  private npcs: Array<{ data: MapNpc; body: Phaser.GameObjects.Rectangle; visual: Phaser.GameObjects.Container; label: Phaser.GameObjects.Text }> = [];
  private exits: Array<{ data: MapExit; zone: Phaser.GameObjects.Zone }> = [];
  private lastPublished = "";
  private nameLabels: Phaser.GameObjects.Text[] = [];
  private transitioning = false;
  private npcLocations: Record<string, MapId> = {};
  private npcWaypoints: Record<string, string> = {};
  private pendingNpcPaths: Record<string, GridPoint[]> = {};
  private playerVisual!: Phaser.GameObjects.Container;
  private facing: FacingDirection = "south";
  private animationClock = 0;

  constructor() {
    super("spatial-scene");
  }

  preload() {
    this.load.image("clubroom-background", clubroomBackground);
    this.load.image("actor-player", playerActor);
    this.load.image("actor-linxi", linxiActor);
    this.load.image("actor-shenzhiyi", shenzhiyiActor);
  }

  create(data: { hooks: Hooks }) {
    this.hooks = data.hooks;
    this.npcLocations = data.hooks.npcLocations ?? {};
    this.npcWaypoints = data.hooks.npcWaypoints ?? {};
    this.cursors = this.input.keyboard!.createCursorKeys();
    this.keys = this.input.keyboard!.addKeys("W,A,S,D,E") as Record<string, Phaser.Input.Keyboard.Key>;
    const initialSpawn = data.hooks.initialPlayer
      ? { x: data.hooks.initialPlayer.x / mapData.tileSize - 0.5, y: data.hooks.initialPlayer.y / mapData.tileSize - 0.5 }
      : undefined;
    this.loadMap(data.hooks.mapId ?? "campus_center", initialSpawn);
  }

  update() {
    if (!this.player || !this.cursors) return;
    this.syncActorVisuals();
    if (this.hooks.isInputLocked?.()) {
      this.player.body.setVelocity(0, 0);
      this.playerVisual?.setScale(1, 1);
      return;
    }
    const left = this.cursors.left.isDown || this.keys.A.isDown;
    const right = this.cursors.right.isDown || this.keys.D.isDown;
    const up = this.cursors.up.isDown || this.keys.W.isDown;
    const down = this.cursors.down.isDown || this.keys.S.isDown;
    const vector = new Phaser.Math.Vector2(Number(right) - Number(left), Number(down) - Number(up));
    const moving = vector.lengthSq() > 0;
    if (moving) {
      this.facing = this.resolveFacing(vector.x, vector.y);
      this.animationClock += 0.18;
      this.playerVisual.setScale(this.facing.startsWith("west") ? -1 : 1, 1 + Math.sin(this.animationClock) * 0.035);
      vector.normalize().scale(PLAYER_SPEED);
    } else {
      this.playerVisual.setScale(this.facing.startsWith("west") ? -1 : 1, 1);
    }
    this.player.body.setVelocity(vector.x, vector.y);
    this.publishState();
  }

  private resolveFacing(x: number, y: number): FacingDirection {
    if (x === 0 && y < 0) return "north";
    if (x > 0 && y < 0) return "north-east";
    if (x > 0 && y === 0) return "east";
    if (x > 0 && y > 0) return "south-east";
    if (x === 0 && y > 0) return "south";
    if (x < 0 && y > 0) return "south-west";
    if (x < 0 && y === 0) return "west";
    return "north-west";
  }

  public interact() {
    const state = this.currentState();
    if (state.nearbyNpc) this.hooks.onState(state);
    if (state.nearbyExit?.endDay) this.hooks.onState(state);
  }

  public switchMap(mapId: MapId, spawn?: { x: number; y: number }) {
    this.loadMap(mapId, spawn);
  }

  public updateNpcLocations(locations: Record<string, MapId>, waypoints: Record<string, string> = this.npcWaypoints) {
    const definition = maps[this.mapId];
    for (const npc of this.npcs) {
      if (locations[npc.data.id] !== this.mapId) continue;
      const waypoint = waypoints[npc.data.id] ?? npc.data.waypoint;
      const target = waypoint ? definition.waypoints[waypoint] : undefined;
      if (!target) continue;
      const start = {
        x: npc.body.x / mapData.tileSize - 0.5,
        y: npc.body.y / mapData.tileSize - 0.5,
      };
      this.pendingNpcPaths[npc.data.id] = findGridPath(definition, start, target);
    }
    this.npcLocations = locations;
    this.npcWaypoints = waypoints;
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
    this.drawSceneSurface(definition);
    this.walls = this.physics.add.staticGroup();
    for (const wall of definition.walls) {
      if (mapId !== "clubroom") this.drawWallBlock(wall);
      const body = this.add.rectangle((wall.x + wall.w / 2) * mapData.tileSize, (wall.y + wall.h / 2) * mapData.tileSize, wall.w * mapData.tileSize, wall.h * mapData.tileSize, 0x000000, 0);
      body.setVisible(false);
      this.physics.add.existing(body, true);
      this.walls.add(body);
    }
    const initial = spawn ?? definition.spawn;
    this.player = this.add.rectangle((initial.x + 0.5) * mapData.tileSize, (initial.y + 0.5) * mapData.tileSize, 18, 18, 0x000000, 0) as PhysicsRectangle;
    this.player.setVisible(false);
    this.physics.add.existing(this.player);
    this.player.setDepth(10);
    this.playerVisual = this.createActorVisual(0xe9e0bd, 0x6e5960, "actor-player");
    this.playerVisual.setPosition(this.player.x, this.player.y).setDepth(20);
    this.physics.add.collider(this.player, this.walls);
    this.cameras.main.startFollow(this.player, true, 0.08, 0.08);
    this.cameras.main.setDeadzone(160, 96);
    for (const npc of definition.npcs) {
      const location = this.npcLocations[npc.id];
      if (!location || location === mapId) this.addNpc(npc);
    }
    this.pendingNpcPaths = {};
    for (const exit of definition.exits) this.addExit(exit);
    this.publishState(true);
  }

  private drawSceneSurface(definition: MapDefinition) {
    if (this.mapId === "clubroom") {
      this.add.image(0, 0, "clubroom-background")
        .setOrigin(0, 0)
        .setDisplaySize(definition.width * mapData.tileSize, definition.height * mapData.tileSize)
        .setDepth(-10);
      return;
    }
    const graphics = this.add.graphics().setDepth(-10);
    const base = Number(definition.background.replace("#", "0x"));
    const light = Phaser.Display.Color.IntegerToColor(base).lighten(7).color;
    const shade = Phaser.Display.Color.IntegerToColor(base).darken(7).color;
    graphics.fillStyle(base, 1);
    graphics.fillRect(0, 0, definition.width * mapData.tileSize, definition.height * mapData.tileSize);
    for (let y = 0; y < definition.height; y += 1) {
      for (let x = 0; x < definition.width; x += 1) {
        graphics.fillStyle((x + y) % 2 === 0 ? light : shade, 0.32);
        graphics.fillRect(x * mapData.tileSize, y * mapData.tileSize, mapData.tileSize, mapData.tileSize);
      }
    }
    graphics.lineStyle(1, 0xffffff, 0.035);
    for (let x = 0; x <= definition.width; x += 1) graphics.lineBetween(x * mapData.tileSize, 0, x * mapData.tileSize, definition.height * mapData.tileSize);
    for (let y = 0; y <= definition.height; y += 1) graphics.lineBetween(0, y * mapData.tileSize, definition.width * mapData.tileSize, y * mapData.tileSize);
    this.drawMapDecorations();
  }

  private drawWallBlock(wall: { x: number; y: number; w: number; h: number }) {
    const graphics = this.add.graphics().setDepth(2);
    const x = wall.x * mapData.tileSize;
    const y = wall.y * mapData.tileSize;
    const w = wall.w * mapData.tileSize;
    const h = wall.h * mapData.tileSize;
    graphics.fillStyle(0x0c1713, 0.22);
    graphics.fillRect(x + 5, y + 8, w, h);
    graphics.fillStyle(0x263b31, 1);
    graphics.fillRect(x, y + 6, w, h - 6);
    graphics.fillStyle(0x46614f, 1);
    graphics.fillRect(x, y, w, Math.min(8, h));
    graphics.lineStyle(1, 0x8eaa8e, 0.34);
    graphics.lineBetween(x, y, x + w, y);
  }

  private drawMapDecorations() {
    if (this.mapId === "campus_center") {
      this.drawWalkway(2, 9, 28, 2, 0xc4b487);
      this.drawFountain(8.5, 9.5);
      this.drawTree(25, 4);
      this.drawTree(27, 15);
    } else if (this.mapId === "arts_hallway") {
      this.drawWalkway(8, 2, 8, 1, 0x6c5a4c);
      for (const x of [3, 5, 18, 20]) this.drawLocker(x, 5);
      this.drawNoticeBoard(12, 8);
    } else if (this.mapId === "clubroom") {
      this.drawWalkway(2, 11, 16, 1, 0x7c5542);
      this.drawRehearsalTable(5, 5);
      this.drawRehearsalTable(15, 5);
      this.drawWindow(15, 2);
      this.drawCurtain(2, 2);
    } else if (this.mapId === "rooftop") {
      this.drawRailing(4, 2, 10);
      this.drawPlanter(4, 11);
      this.drawPlanter(13, 11);
    }
  }

  private drawWalkway(x: number, y: number, w: number, h: number, color: number) {
    const graphics = this.add.graphics().setDepth(1);
    graphics.fillStyle(0x372c24, 0.24);
    graphics.fillRect(x * mapData.tileSize + 4, y * mapData.tileSize + 7, w * mapData.tileSize, h * mapData.tileSize);
    graphics.fillStyle(color, 0.8);
    graphics.fillRect(x * mapData.tileSize, y * mapData.tileSize, w * mapData.tileSize, h * mapData.tileSize);
    graphics.lineStyle(1, 0xf4dfb0, 0.22);
    for (let tile = 1; tile < w; tile += 1) graphics.lineBetween((x + tile) * mapData.tileSize, y * mapData.tileSize, (x + tile) * mapData.tileSize, (y + h) * mapData.tileSize);
  }

  private drawFountain(x: number, y: number) {
    const graphics = this.add.graphics().setDepth(y * mapData.tileSize / 1000);
    graphics.fillStyle(0x20342c, 0.25);
    graphics.fillEllipse(x * mapData.tileSize, (y + 0.22) * mapData.tileSize, 76, 22);
    graphics.fillStyle(0x806c52, 1);
    graphics.fillEllipse(x * mapData.tileSize, y * mapData.tileSize, 70, 24);
    graphics.fillStyle(0x86b9c2, 1);
    graphics.fillEllipse(x * mapData.tileSize, (y - 0.2) * mapData.tileSize, 52, 18);
    graphics.fillStyle(0xc6e8dc, 0.8);
    graphics.fillCircle(x * mapData.tileSize, (y - 0.65) * mapData.tileSize, 6);
  }

  private drawTree(x: number, y: number) {
    const graphics = this.add.graphics().setDepth(y * mapData.tileSize / 1000);
    graphics.fillStyle(0x17261f, 0.3);
    graphics.fillEllipse(x * mapData.tileSize, (y + 0.35) * mapData.tileSize, 42, 15);
    graphics.fillStyle(0x76513a, 1);
    graphics.fillRect(x * mapData.tileSize - 5, y * mapData.tileSize, 10, 25);
    graphics.fillStyle(0x315c46, 1);
    graphics.fillCircle(x * mapData.tileSize, (y - 0.28) * mapData.tileSize, 22);
    graphics.fillStyle(0x4d8060, 1);
    graphics.fillCircle((x - 0.35) * mapData.tileSize, (y - 0.42) * mapData.tileSize, 12);
  }

  private drawLocker(x: number, y: number) {
    const graphics = this.add.graphics().setDepth(y * mapData.tileSize / 1000);
    graphics.fillStyle(0x2d3c46, 1);
    graphics.fillRect(x * mapData.tileSize, y * mapData.tileSize, 26, 52);
    graphics.lineStyle(1, 0x9eb3b2, 0.42);
    graphics.strokeRect(x * mapData.tileSize + 3, y * mapData.tileSize + 4, 20, 44);
    graphics.fillStyle(0xe1bb64, 1);
    graphics.fillCircle(x * mapData.tileSize + 18, y * mapData.tileSize + 27, 2);
  }

  private drawNoticeBoard(x: number, y: number) {
    const graphics = this.add.graphics().setDepth(y * mapData.tileSize / 1000);
    graphics.fillStyle(0x543d2d, 1);
    graphics.fillRect(x * mapData.tileSize, y * mapData.tileSize, 50, 54);
    graphics.fillStyle(0xe2c789, 1);
    graphics.fillRect(x * mapData.tileSize + 6, y * mapData.tileSize + 6, 38, 30);
  }

  private drawRehearsalTable(x: number, y: number) {
    const graphics = this.add.graphics().setDepth(y * mapData.tileSize / 1000);
    graphics.fillStyle(0x34251e, 0.28);
    graphics.fillEllipse(x * mapData.tileSize, (y + 0.42) * mapData.tileSize, 92, 20);
    graphics.fillStyle(0x754b36, 1);
    graphics.fillRect(x * mapData.tileSize - 38, y * mapData.tileSize - 8, 76, 20);
    graphics.fillStyle(0xb2764e, 1);
    graphics.fillRect(x * mapData.tileSize - 38, y * mapData.tileSize - 8, 76, 6);
    graphics.fillStyle(0xe1c98a, 1);
    graphics.fillRect(x * mapData.tileSize - 14, y * mapData.tileSize - 3, 28, 9);
  }

  private drawWindow(x: number, y: number) {
    const graphics = this.add.graphics().setDepth(y * mapData.tileSize / 1000);
    graphics.fillStyle(0x222d35, 1);
    graphics.fillRect(x * mapData.tileSize - 34, y * mapData.tileSize - 10, 68, 44);
    graphics.fillStyle(0x8eb5b5, 1);
    graphics.fillRect(x * mapData.tileSize - 27, y * mapData.tileSize - 3, 54, 26);
    graphics.lineStyle(2, 0xe2c789, 0.6);
    graphics.lineBetween(x * mapData.tileSize, y * mapData.tileSize - 3, x * mapData.tileSize, y * mapData.tileSize + 23);
  }

  private drawCurtain(x: number, y: number) {
    const graphics = this.add.graphics().setDepth(y * mapData.tileSize / 1000);
    graphics.fillStyle(0x8d4650, 1);
    graphics.fillRect(x * mapData.tileSize, y * mapData.tileSize - 8, 30, 68);
    graphics.fillStyle(0xb96b70, 0.8);
    graphics.fillRect(x * mapData.tileSize + 7, y * mapData.tileSize - 8, 8, 68);
  }

  private drawRailing(x: number, y: number, length: number) {
    const graphics = this.add.graphics().setDepth(y * mapData.tileSize / 1000);
    graphics.lineStyle(4, 0x3d504c, 1);
    graphics.lineBetween(x * mapData.tileSize, y * mapData.tileSize, (x + length) * mapData.tileSize, y * mapData.tileSize);
    graphics.lineStyle(2, 0xa8b9a0, 0.7);
    for (let i = 0; i <= length; i += 2) graphics.lineBetween((x + i) * mapData.tileSize, y * mapData.tileSize, (x + i) * mapData.tileSize, (y + 0.7) * mapData.tileSize);
  }

  private drawPlanter(x: number, y: number) {
    const graphics = this.add.graphics().setDepth(y * mapData.tileSize / 1000);
    graphics.fillStyle(0x2b372f, 0.26);
    graphics.fillEllipse(x * mapData.tileSize, (y + 0.35) * mapData.tileSize, 48, 14);
    graphics.fillStyle(0x8b5e42, 1);
    graphics.fillRect(x * mapData.tileSize - 22, y * mapData.tileSize, 44, 18);
    graphics.fillStyle(0x4f7c56, 1);
    graphics.fillCircle(x * mapData.tileSize, (y - 0.18) * mapData.tileSize, 14);
  }

  private createActorVisual(bodyColor: number, accentColor: number, textureKey?: string): Phaser.GameObjects.Container {
    const container = this.add.container(0, 0);
    const graphics = this.add.graphics();
    graphics.fillStyle(0x14221c, 0.35);
    graphics.fillEllipse(0, 14, 26, 9);
    if (textureKey) {
      const sprite = this.add.image(0, 10, textureKey).setOrigin(0.5, 1).setScale(0.72);
      container.add([graphics, sprite]);
      return container;
    }
    graphics.fillStyle(bodyColor, 1);
    graphics.fillRoundedRect(-10, -1, 20, 20, 5);
    graphics.fillStyle(0xf2c9a5, 1);
    graphics.fillCircle(0, -10, 8);
    graphics.fillStyle(accentColor, 1);
    graphics.fillRoundedRect(-9, -17, 18, 8, 4);
    graphics.fillStyle(0xf8f0dc, 0.85);
    graphics.fillRect(-5, 4, 10, 3);
    container.add(graphics);
    return container;
  }

  private syncActorVisuals() {
    if (this.playerVisual && this.player) {
      this.playerVisual.setPosition(this.player.x, this.player.y).setDepth(20 + this.player.y / 10000);
    }
    for (const npc of this.npcs) {
      npc.visual.setPosition(npc.body.x, npc.body.y).setDepth(15 + npc.body.y / 10000);
      npc.label.setPosition(npc.body.x, npc.body.y - 28).setDepth(30 + npc.body.y / 10000);
    }
  }

  private addNpc(npc: MapNpc) {
    const waypoint = this.npcWaypoints[npc.id] ?? npc.waypoint;
    const anchor = waypoint ? maps[this.mapId].waypoints[waypoint] : undefined;
    const x = anchor?.x ?? npc.x;
    const y = anchor?.y ?? npc.y;
    const path = this.pendingNpcPaths[npc.id];
    delete this.pendingNpcPaths[npc.id];
    const first = path?.[0] ?? { x, y };
    const body = this.add.rectangle((first.x + 0.5) * mapData.tileSize, (first.y + 0.5) * mapData.tileSize, 18, 18, 0x000000, 0);
    body.setVisible(false);
    const actorTexture = npc.id === "linxi" ? "actor-linxi" : npc.id === "shenzhiyi" ? "actor-shenzhiyi" : undefined;
    const visual = this.createActorVisual(Number(npc.color.replace("#", "0x")), npc.id === "linxi" ? 0x8a4e5a : 0x59658c, actorTexture);
    visual.setPosition(body.x, body.y).setDepth(15 + body.y / 10000);
    const label = this.add.text(body.x, body.y - 28, npc.label, { color: "#fff8e5", fontFamily: "sans-serif", fontSize: "13px", backgroundColor: "#17231fcc", padding: { x: 4, y: 2 } }).setOrigin(0.5, 1).setDepth(20);
    this.npcs.push({ data: npc, body, visual, label });
    this.nameLabels.push(label);
    if (path && path.length > 1) this.animateNpcPath(body, path, 1);
  }

  private animateNpcPath(body: Phaser.GameObjects.Rectangle, path: GridPoint[], index: number): void {
    const point = path[index];
    if (!point) return;
    this.tweens.add({
      targets: body,
      x: (point.x + 0.5) * mapData.tileSize,
      y: (point.y + 0.5) * mapData.tileSize,
      duration: 110,
      ease: "Linear",
      onComplete: () => this.animateNpcPath(body, path, index + 1),
    });
  }

  private addExit(exit: MapExit) {
    const zone = this.add.zone((exit.x + exit.w / 2) * mapData.tileSize, (exit.y + exit.h / 2) * mapData.tileSize, exit.w * mapData.tileSize, exit.h * mapData.tileSize);
    this.physics.add.existing(zone, true);
    this.physics.add.overlap(this.player, zone, async () => {
      if (this.transitioning || exit.endDay) return;
      this.transitioning = true;
      const serverState = await this.hooks.onTransition?.({ fromMap: this.mapId, exitId: exit.id });
      if (serverState) {
        if (serverState.npcLocations) this.npcLocations = serverState.npcLocations;
        if (serverState.npcWaypoints) this.npcWaypoints = serverState.npcWaypoints;
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
      nearbyNpc: nearestNpc ? {
        id: nearestNpc.id,
        label: nearestNpc.label,
        interaction: nearestNpc.interaction,
        kind: nearestNpc.kind ?? "major",
        bubble: nearestNpc.bubble,
      } : null,
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
