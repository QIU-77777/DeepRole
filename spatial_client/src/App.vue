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
let game: Phaser.Game | null = null;

const mapLabels: Record<MapId, string> = {
  campus_center: "校园中心",
  arts_hallway: "艺术楼走廊",
  clubroom: "剧社活动室",
  rooftop: "天台",
};

const interactionHint = computed(() => {
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
    return { mapId: state.player.map_id as MapId, x: state.player.x, y: state.player.y };
  } catch {
    message.value = "出口服务暂不可用，使用本地灰盒切换。";
    return null;
  }
}

function interact() {
  if (gameState.value.nearbyNpc) {
    panelOpen.value = true;
    message.value = `已接近${gameState.value.nearbyNpc.label}。可以面对面交谈。`;
    return;
  }
  if (gameState.value.nearbyExit?.endDay) {
    message.value = "日结与叙事时间推进将在空间状态里程碑接入。";
    return;
  }
  if (gameState.value.nearbyExit) message.value = `前往${mapLabels[gameState.value.nearbyExit.target]}。`;
}

function appendDialogue(author: string, content: string, kind = "") {
  if (content.trim()) dialogueMessages.value.push({ author, content: content.trim(), kind });
}

async function sendDialogue(content = dialogueInput.value) {
  const text = content.trim();
  const target = gameState.value.nearbyNpc;
  if (!text || !target || dialogueBusy.value) return;
  dialogueInput.value = "";
  dialogueBusy.value = true;
  dialogueChoices.value = [];
  appendDialogue("我", text, "player");
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({
        message: text,
        spatial: {
          map_id: gameState.value.mapId,
          primary_target: target.id,
          visible_to: gameState.value.mapId === "clubroom" ? ["linxi", "shenzhiyi"] : [target.id],
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
        const event = raw.match(/^event:\s*(.+)$/m)?.[1]?.trim();
        const dataText = raw.match(/^data:\s*(.+)$/m)?.[1];
        if (!event || !dataText) continue;
        const data = JSON.parse(dataText) as { author?: string; content?: string; choices?: string[] };
        if (event === "narrator") appendDialogue(data.author ?? "旁白", data.content ?? "", "narrator");
        if (event === "agent") appendDialogue(data.author ?? target.label, data.content ?? "", "agent");
        if (event === "choices") dialogueChoices.value = data.choices ?? [];
        if (event === "response_done") dialogueBusy.value = false;
      }
      if (done) break;
    }
    dialogueBusy.value = false;
  } catch {
    dialogueBusy.value = false;
    appendDialogue("系统", "对话服务暂不可用，请稍后再试。", "error");
  }
}

onMounted(async () => {
  window.addEventListener("keydown", onKeyDown);
  try {
    const response = await fetch("/api/spatial/state");
    if (response.ok) {
      const state = await response.json();
      gameTime.value = state.story_time.display;
    }
  } catch {
    message.value = "空间状态服务未连接，使用本地灰盒初始状态。";
  }
  if (gameHost.value) {
    game = createSpatialGame(gameHost.value, {
      onState: syncSnapshot,
      onTransition: transition,
      isInputLocked: () => panelOpen.value,
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
  if (event.key === "Escape" && !dialogueBusy.value) panelOpen.value = false;
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
    <div class="interaction-hint">{{ interactionHint }}</div>
    <section v-if="panelOpen" class="dialogue-panel" aria-live="polite">
      <button class="close-button" type="button" :disabled="dialogueBusy" @click="panelOpen = false">×</button>
      <p class="eyebrow">面对面交谈</p>
      <h2>{{ gameState.nearbyNpc?.label }}</h2>
      <div class="dialogue-log">
        <p v-for="(item, index) in dialogueMessages" :key="index" :class="['dialogue-line', item.kind]">
          <strong>{{ item.author }}</strong><span>{{ item.content }}</span>
        </p>
        <p v-if="dialogueBusy" class="dialogue-line narrator"><strong>旁白</strong><span>……</span></p>
      </div>
      <div class="dialogue-mode">公开 · 当前场景中的主要角色可听见</div>
      <div v-if="dialogueChoices.length" class="dialogue-choices">
        <button v-for="choice in dialogueChoices" :key="choice" type="button" :disabled="dialogueBusy" @click="sendDialogue(choice)">{{ choice }}</button>
      </div>
      <form class="dialogue-composer" @submit.prevent="sendDialogue()">
        <input v-model="dialogueInput" :disabled="dialogueBusy" placeholder="说点什么……" aria-label="对话输入" />
        <button type="submit" :disabled="dialogueBusy || !dialogueInput.trim()">发送</button>
      </form>
    </section>
  </main>
</template>
