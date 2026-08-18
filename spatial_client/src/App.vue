<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { createSpatialGame, type MapId, type SpatialGameState } from "./game";
import type Phaser from "phaser";

const gameHost = ref<HTMLElement | null>(null);
const gameState = ref<SpatialGameState>({ mapId: "campus_center", player: { x: 0, y: 0 }, nearbyNpc: null, nearbyExit: null });
const gameTime = ref("秋季 · 第 1 周 · 周三 18:30");
const message = ref("在校园中心自由探索。靠近人物或出口后按 E。 ");
const panelOpen = ref(false);
const gameReady = ref(false);
const dialogueInput = ref("");
const dialogueBusy = ref(false);
const dialogueChoices = ref<string[]>([]);
const dialogueMessages = ref<Array<{ author: string; content: string; kind?: string }>>([]);
const dialogueVisibility = ref<"public" | "private">("public");
const dialogueRetryText = ref("");
const profileOpen = ref(false);
const profileLoading = ref(false);
const profileAgent = ref("linxi");
const profileData = ref<{ selected_display_name?: string; nodes?: Array<{ id: string; label?: string; group?: string; meta?: { content?: string; content_preview?: string; raw_dialogue_preview?: string; type_label?: string } }>; stats?: { episode_count?: number; understanding_count?: number } } | null>(null);
const relationshipData = ref<Record<string, { stage: string; tags: string[]; description: string }>>({});
const saveBusy = ref(false);
const saveNotice = ref("");
const saves = ref<Array<{ filename: string; title?: string; created_at?: string }>>([]);
const availableEvent = ref<{ event_id: string; label: string; prompt: string } | null>(null);
let npcLocations: Record<string, MapId> = {};
let dialogueController: AbortController | null = null;
let game: Phaser.Game | null = null;

const mapLabels: Record<MapId, string> = {
  campus_center: "校园中心",
  arts_hallway: "艺术楼走廊",
  clubroom: "剧社活动室",
  rooftop: "天台",
};

const interactionHint = computed(() => {
  if (gameState.value.nearbyNpc?.kind === "ambient") return `E 查看 · ${gameState.value.nearbyNpc.label}`;
  if (gameState.value.nearbyNpc) return `E 交谈 · ${gameState.value.nearbyNpc.label}`;
  if (gameState.value.nearbyExit?.endDay) return "E 返回宿舍并结束今天";
  if (gameState.value.nearbyExit) return `E 进入 · ${mapLabels[gameState.value.nearbyExit.target]}`;
  return "WASD / 方向键移动";
});

async function syncSnapshot(state: SpatialGameState) {
  gameState.value = state;
  if (!gameReady.value) return;
  try {
    await fetch("/api/spatial/snapshot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ map_id: state.mapId, x: state.player.x, y: state.player.y }),
    });
  } catch {
    message.value = "空间状态服务暂不可用，当前仍可继续灰盒探索。";
  }
}

async function transition(request: { fromMap: MapId; exitId: string }) {
  try {
    const response = await fetch("/api/spatial/transition", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from_map: request.fromMap, exit_id: request.exitId }),
    });
    if (!response.ok) return null;
    const state = await response.json();
    gameTime.value = state.story_time.display;
    availableEvent.value = state.available_events?.[0] ?? null;
    return { mapId: state.player.map_id as MapId, x: state.player.x, y: state.player.y };
  } catch {
    message.value = "出口服务暂不可用，使用本地灰盒切换。";
    return null;
  }
}

async function triggerEvent() {
  const event = availableEvent.value;
  if (!event) return;
  const response = await fetch("/api/spatial/event/trigger", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event_id: event.event_id }),
  });
  if (!response.ok) return;
  message.value = event.prompt;
  availableEvent.value = null;
}

function interact() {
  if (gameState.value.nearbyNpc?.kind === "ambient") {
    message.value = gameState.value.nearbyNpc.bubble || `${gameState.value.nearbyNpc.label}从你身边经过。`;
    return;
  }
  if (gameState.value.nearbyNpc) {
    panelOpen.value = true;
    message.value = `已接近${gameState.value.nearbyNpc.label}。可以面对面交谈。`;
    return;
  }
  if (gameState.value.nearbyExit?.endDay) {
    void endDay();
    return;
  }
  if (gameState.value.nearbyExit) message.value = `前往${mapLabels[gameState.value.nearbyExit.target]}。`;
}

async function endDay() {
  try {
    const response = await fetch("/api/spatial/end-day", { method: "POST" });
    if (!response.ok) throw new Error("end day failed");
    const state = await response.json();
    gameTime.value = state.story_time.display;
    const scene = game?.scene.getScene("spatial-scene") as { switchMap?: (mapId: MapId, spawn: { x: number; y: number }) => void } | undefined;
    scene?.switchMap?.("campus_center", {
      x: state.player.x / 32 - 0.5,
      y: state.player.y / 32 - 0.5,
    });
    message.value = "你回到宿舍休息。新的一天开始了。";
  } catch {
    message.value = "现在还不能结束今天。请从校园中心的宿舍出口离开。";
  }
}

async function openProfile(agent = gameState.value.nearbyNpc?.kind === "major" ? gameState.value.nearbyNpc.id : profileAgent.value) {
  profileAgent.value = agent;
  profileOpen.value = true;
  profileLoading.value = true;
  try {
    const [graphResponse, relationshipResponse] = await Promise.all([
      fetch(`/api/memory-graph?agent=${encodeURIComponent(agent)}`),
      fetch("/api/relationships"),
    ]);
    if (!graphResponse.ok) throw new Error("profile failed");
    profileData.value = await graphResponse.json();
    if (relationshipResponse.ok) {
      const payload = await relationshipResponse.json();
      relationshipData.value = payload.characters ?? {};
    }
  } catch {
    message.value = "人物档案暂不可用。";
  } finally {
    profileLoading.value = false;
  }
}

async function saveWorldline(): Promise<boolean> {
  if (saveBusy.value) return false;
  saveBusy.value = true;
  saveNotice.value = "正在等待后台记忆整理……";
  try {
    const response = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "save failed");
    saveNotice.value = `世界线已保存：${payload.filename}`;
    await refreshSaves();
    return true;
  } catch (error) {
    saveNotice.value = error instanceof Error ? error.message : "保存失败。";
    return false;
  } finally {
    saveBusy.value = false;
  }
}

async function refreshSaves() {
  const response = await fetch("/api/saves");
  if (!response.ok) return;
  const payload = await response.json();
  saves.value = payload.saves ?? [];
}

async function loadWorldline(filename: string) {
  if (!window.confirm("即将读取旧世界线。是否继续进入读取确认？")) return;
  const saveFirst = window.confirm("是否先创建一个手动保存节点？确定保存，取消则不创建节点。 ");
  if (saveFirst && !(await saveWorldline())) return;
  if (!window.confirm("确认读取这个旧世界线？未手动保存的中间状态将无法回滚。")) return;
  const response = await fetch("/api/load", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename }),
  });
  if (!response.ok) {
    saveNotice.value = "读取失败，请稍后再试。";
    return;
  }
  window.location.reload();
}

function closeSaves() {
  saves.value = [];
}

function appendDialogue(author: string, content: string, kind = "") {
  if (content.trim()) dialogueMessages.value.push({ author, content: content.trim(), kind });
}

async function sendDialogue(content = dialogueInput.value, recordPlayer = true) {
  const text = content.trim();
  const target = gameState.value.nearbyNpc;
  if (!text || !target || target.kind !== "major" || dialogueBusy.value) return;
  dialogueInput.value = "";
  dialogueBusy.value = true;
  dialogueChoices.value = [];
  dialogueRetryText.value = "";
  const controller = new AbortController();
  dialogueController = controller;
  if (recordPlayer) appendDialogue("我", text, "player");
  let receivedDone = false;
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      signal: controller.signal,
      body: JSON.stringify({
        message: text,
        spatial: {
          map_id: gameState.value.mapId,
          primary_target: target.id,
          visible_to: dialogueVisibility.value === "public" && gameState.value.mapId === "clubroom"
            ? ["linxi", "shenzhiyi"]
            : [target.id],
        },
      }),
    });
    if (!response.ok || !response.body) throw new Error("dialogue request failed");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
      const events = buffer.split("\n\n");
      buffer = events.pop() ?? "";
      for (const raw of events) {
        const dataText = raw.match(/^data:\s*(.+)$/m)?.[1];
        if (!dataText) continue;
        const data = JSON.parse(dataText) as {
          type?: string;
          author?: string;
          content?: string;
          choices?: string[];
          story_time?: { display?: string };
          npc_locations?: Record<string, MapId>;
          available_events?: Array<{ event_id: string; label: string; prompt: string }>;
        };
        const event = data.type;
        if (!event) continue;
        if (event === "narrator") {
          appendDialogue(data.author ?? "旁白", data.content ?? "", "narrator");
          message.value = data.content ?? message.value;
        }
        if (event === "agent") appendDialogue(data.author ?? target.label, data.content ?? "", "agent");
        if (event === "choices") dialogueChoices.value = data.choices ?? [];
        if (event === "done") receivedDone = true;
        if (event === "spatial_state") {
          if (data.story_time?.display) gameTime.value = data.story_time.display;
          availableEvent.value = data.available_events?.[0] ?? availableEvent.value;
          if (data.npc_locations) {
            const scene = game?.scene.getScene("spatial-scene") as { updateNpcLocations?: (locations: Record<string, MapId>) => void } | undefined;
            scene?.updateNpcLocations?.(data.npc_locations);
          }
        }
        if (event === "response_done") dialogueBusy.value = false;
      }
      if (done) break;
    }
    if (!receivedDone && !controller.signal.aborted) throw new Error("dialogue stream interrupted");
    dialogueBusy.value = false;
  } catch {
    dialogueBusy.value = false;
    if (controller.signal.aborted) {
      appendDialogue("系统", "本轮对话已停止。", "error");
    } else {
      dialogueRetryText.value = text;
      appendDialogue("系统", "对话服务暂不可用，请稍后再试。", "error");
    }
  } finally {
    if (dialogueController === controller) dialogueController = null;
  }
}

function cancelDialogue() {
  dialogueController?.abort();
  dialogueBusy.value = false;
}

function closeDialogue() {
  if (dialogueBusy.value) cancelDialogue();
  else panelOpen.value = false;
}

onMounted(async () => {
  window.addEventListener("keydown", onKeyDown);
  try {
    const response = await fetch("/api/spatial/state");
    if (response.ok) {
      const state = await response.json();
      gameTime.value = state.story_time.display;
      npcLocations = state.npc_locations ?? {};
      availableEvent.value = state.available_events?.[0] ?? null;
    }
  } catch {
    message.value = "空间状态服务未连接，使用本地灰盒初始状态。";
  }
  if (gameHost.value) {
    game = createSpatialGame(gameHost.value, {
      onState: syncSnapshot,
      onTransition: transition,
      isInputLocked: () => panelOpen.value,
      npcLocations,
    });
    gameReady.value = true;
  }
});

onUnmounted(() => {
  window.removeEventListener("keydown", onKeyDown);
  game?.destroy(true);
});

function onKeyDown(event: KeyboardEvent) {
  const target = event.target as HTMLElement | null;
  const typing = target?.tagName === "INPUT" || target?.tagName === "TEXTAREA";
  if (event.key.toLowerCase() === "e" && !panelOpen.value && !typing) interact();
  if (event.key === "Escape") closeDialogue();
}
</script>

<template>
  <main class="spatial-shell">
    <section ref="gameHost" class="game-host" aria-label="空间化地图"></section>
    <header class="hud hud-top">
      <div>
        <p class="eyebrow">未名剧社 · 空间化垂直切片</p>
        <h1>{{ mapLabels[gameState.mapId] }}</h1>
      </div>
      <div class="time-card">
        <span>{{ gameTime }}</span>
        <small>叙事时间 · 秋季</small>
        <button class="profile-button" type="button" @click="openProfile()">人物档案</button>
        <div class="save-controls">
          <button type="button" :disabled="saveBusy" @click="saveWorldline">保存世界线</button>
          <button type="button" @click="refreshSaves">读取列表</button>
        </div>
      </div>
    </header>
    <aside class="minimap" aria-label="小地图">
      <span class="minimap-title">当前区域</span>
      <strong>{{ mapLabels[gameState.mapId] }}</strong>
      <div class="minimap-dots">
        <i :class="{ active: gameState.mapId === 'campus_center' }"></i>
        <i :class="{ active: gameState.mapId === 'arts_hallway' }"></i>
        <i :class="{ active: gameState.mapId === 'clubroom' }"></i>
        <i :class="{ active: gameState.mapId === 'rooftop' }"></i>
      </div>
    </aside>
    <div class="narration-bar">{{ message }}</div>
    <button v-if="availableEvent" class="event-hint" type="button" @click="triggerEvent">发现：{{ availableEvent.label }}</button>
    <div class="interaction-hint">{{ interactionHint }}</div>
    <section v-if="panelOpen" class="dialogue-panel" aria-live="polite">
      <button class="close-button" type="button" @click="closeDialogue">{{ dialogueBusy ? "停止" : "×" }}</button>
      <p class="eyebrow">面对面交谈</p>
      <h2>{{ gameState.nearbyNpc?.label }}</h2>
      <div class="dialogue-log">
        <p v-for="(item, index) in dialogueMessages" :key="index" :class="['dialogue-line', item.kind]">
          <strong>{{ item.author }}</strong><span>{{ item.content }}</span>
        </p>
        <p v-if="dialogueBusy" class="dialogue-line narrator"><strong>旁白</strong><span>……</span></p>
      </div>
      <label class="dialogue-mode">
        <span>听众</span>
        <select v-model="dialogueVisibility" :disabled="dialogueBusy">
          <option value="public">公开 · 在场角色可听见</option>
          <option value="private">私语 · 仅 {{ gameState.nearbyNpc?.label }} 听见</option>
        </select>
      </label>
      <div v-if="dialogueChoices.length" class="dialogue-choices">
        <button v-for="choice in dialogueChoices" :key="choice" type="button" :disabled="dialogueBusy" @click="sendDialogue(choice)">{{ choice }}</button>
      </div>
      <form class="dialogue-composer" @submit.prevent="sendDialogue()">
        <input v-model="dialogueInput" :disabled="dialogueBusy" placeholder="说点什么……" aria-label="对话输入" />
        <button type="submit" :disabled="dialogueBusy || !dialogueInput.trim()">发送</button>
      </form>
      <button v-if="dialogueRetryText && !dialogueBusy" class="retry-dialogue" type="button" @click="sendDialogue(dialogueRetryText, false)">重试上一句</button>
    </section>
    <aside v-if="profileOpen" class="profile-panel" aria-live="polite">
      <button class="close-button" type="button" @click="profileOpen = false">×</button>
      <p class="eyebrow">人物档案 · 私有记忆</p>
      <h2>{{ profileData?.selected_display_name ?? profileAgent }}</h2>
      <div v-if="relationshipData[profileAgent]" class="profile-relation">
        <strong>{{ relationshipData[profileAgent].stage }}</strong>
        <span>{{ relationshipData[profileAgent].description }}</span>
      </div>
      <p v-if="profileLoading">正在读取记忆图……</p>
      <template v-else>
        <p class="profile-stats">事件 {{ profileData?.stats?.episode_count ?? 0 }} · 理解 {{ profileData?.stats?.understanding_count ?? 0 }}</p>
        <div class="profile-memory-list">
          <article v-for="node in profileData?.nodes ?? []" :key="node.id">
            <small>{{ node.group === "understanding" ? "长期理解" : "经历" }}</small>
            <strong>{{ node.label }}</strong>
            <p>{{ node.meta?.content || node.meta?.content_preview || "（暂无详细内容）" }}</p>
            <pre v-if="node.meta?.raw_dialogue_preview">{{ node.meta.raw_dialogue_preview }}</pre>
          </article>
        </div>
      </template>
    </aside>
    <aside v-if="saves.length" class="save-panel" aria-live="polite">
      <button class="close-button" type="button" @click="closeSaves">×</button>
      <p class="eyebrow">手动世界线</p>
      <p v-if="saveNotice" class="save-notice">{{ saveNotice }}</p>
      <button v-for="save in saves" :key="save.filename" type="button" class="save-row" @click="loadWorldline(save.filename)">
        <strong>{{ save.title || save.filename }}</strong><small>{{ save.created_at || save.filename }}</small>
      </button>
    </aside>
  </main>
</template>
