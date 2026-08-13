Multi-Agent Roleplay / Narrative Game Project. The current implementation is built around **FastAPI + pydantic-ai + Pydantic structured output + file-based memory + sqlite-vec**, using `uv` as the project manager.

## Core Design

- **Independent Memory**: Characters maintain their own `memory.jsonl / status.md`; the `narrator` maintains `status.md` and raw history
- **Information Asymmetry**: Messages are scoped via `visible_to`; characters not present in a scene cannot see that round's content
- **Narrator-First**: The `narrator` handles routing and scene progression before sequentially invoking target characters
- **Structured Output**: All structured agents use `PromptedOutput`, not XML; the system reads typed fields directly and writes them back to files
- **Dual-Layer Memory**: Markdown files are human-readable and editable; the vector store handles retrieval

## Tech Stack

- Python 3.11+
- FastAPI + SSE (Server-Sent Events)
- pydantic-ai (`pydantic-ai`) — `Agent` / `PromptedOutput` / `OpenAIChatModel`
- sqlite-vec + aiosqlite
- asyncio

## Current Project Structure

```text
deeprole-memos/
├── server.py                   # FastAPI/SSE entry (delivery layer)
├── config.toml                 # Non-secret runtime parameters
├── data/
│   ├── runtime/                # Runtime state (ignored by git)
│   │   ├── characters/         # Runtime character data
│   │   └── vectors.sqlite      # Vector store
│   ├── templates/              # Story templates (drama)
├── models/                     # Domain layer (innermost): entities + value objects + pure domain rules, zero I/O
│   ├── character.py            # Character entity + get_display_name / extract_identity (derive from soul)
│   ├── memory.py               # EpisodeMemory / Understanding / UnderstandingHistoryEntry
│   ├── dates.py                # Game date value object (parse_cn_date / canonical_cn_date / game_day_*)
│   └── status_fields.py        # status.md known field-name constants
├── repository/                 # Infrastructure layer: all outward I/O (files / sqlite-vec / LLM clients / logging)
│   ├── config.py               # App config: paths, runtime params, character_path, get_agent_names
│   ├── agent_files.py          # Basic per-agent file IO + JSON sidecar + backup
│   ├── status_file.py          # status.md section engine (prose fields) + extract_status_field
│   ├── intent_queue.py         # 打算/待触发事件 typed-list store (intents.json / pending_events.json)
│   ├── runtime_state.py        # Global narrator turn counter + player name
│   ├── memory_store.py         # memory/understanding/draft JSONL IO + normalize (memory text format)
│   ├── character_repo.py       # CharacterRepository: soul cache + load→entity + writeback policy
│   ├── narrator_repo.py        # NarratorRepository: status / world_schedule read-write
│   ├── history.py              # Narrator raw JSONL dialogue history read
│   ├── message_router.py       # Dialogue write / visibility filtering
│   ├── narrator_output.py      # message helpers (extract_narrator_output / raw_message_text / role_to_speaker)
│   ├── save_manager.py         # Save / load / reset / opening load
│   ├── vector_store.py         # sqlite-vec vector storage (write/delete + raw candidate retrieval)
│   ├── sdk_runner.py           # SDK Runner invocation (run_structured_agent / run_app_agent), Logfire trace
│   ├── llm/                    # config (LLM_* parsing) / embedding / rerank clients
│   └── log_config/             # Logfire + business logger configuration (routing_logger / log_file_updates)
├── app/                        # Use-case layer: single-round orchestration + agent assembly + LLM DTOs
│   ├── conversation_flow.py    # Single-round dialogue orchestration and UI adapter functions
│   ├── conversation_service.py # Single character turn: retrieve memory → prompt → SDK → writeback
│   ├── narrator_service.py     # Narrator routing + state_updater orchestration (unpacks LLM DTOs → repos)
│   ├── character_factory.py    # New character incubation
│   ├── prompt_builder.py       # user-message / history window; build_status_block + render_player_relations
│   ├── memory_query_builder.py # Character retrieval query construction (RetrievalQueries)
│   ├── responses.py            # LLM response post-processing (clean_response / strip_thinking / is_valid_response)
│   ├── agent_factory.py        # Agent creation, registry, SDK model config, build_system_prompt
│   ├── llm_schema.py           # LLM structured output contracts (all LLM* prefixed)
│   ├── consolidation/          # Background memory consolidation (flow + inputs)
│   └── memory/                 # Retrieval pipeline (retrieval) + vector index rebuild (indexer)
├── prompts/                    # Prompt constant modules grouped by lifecycle
│   ├── consolidation_prompts.py  # Background consolidation: EpisodeClosureDetector / EpisodeMemoryGenerator / understanding
│   ├── runtime_prompts.py        # Main dialogue: character / narrator / choices / state_updater
│   ├── worldgen_prompts.py       # Character incubation
│   └── opening_intro.txt         # Gameplay intro text (player-facing)
├── scripts/                    # Maintenance scripts
├── static/                     # Alpine.js + HTML/JS frontend
├── tests/                      # pytest tests
├── README.md
├── AGENTS.md
├── CLAUDE.md
└── .env
```

### Layered Dependency Direction

```
models/        ← (self only)            # innermost domain layer: entities + value objects + 纯领域规则
repository/    ← models/                 # infra: files / vector / LLM clients / logging; must NOT import app/ or server
app/           ← models/ + repository/   # use cases: orchestration + agent assembly + LLM* DTOs + response post-processing
server.py      ← all
```

无独立 `shared/`——原 shared 已按归属拆散：游戏日期值对象与 soul 派生规则进 `models/`，config / status 格式解析 / 消息助手进 `repository/`，LLM 输出后处理进 `app/`（判据：领域无关才配做通用工具，否则它有自己的层）。

依赖方向由 `tests/test_layer_dependencies.py` 用 AST 静态强制（新增反向 import 会让对应用例失败）：models 不依赖任何外层；repository 不反依赖 app/server；app 不依赖 server。LLM 调用收敛在 `repository/`（sdk_runner + llm/ 客户端），agent 装配与 LLM* DTO 在 `app/`，consolidation/memory 检索流水线是 `app/` 子包。

## Runtime File Responsibilities

### Character Files

- `soul.md`: Handwritten character definition, read-only; divided into six sections: `<identity>` / `<goal>` / `<past>` / `<habits>` / `<reactions>` / `<voice>`. The `<goal>` section describes the character's deepest inner desires (desires around ability/achievement + deep relational longing)
- `memory.jsonl`: Character long-term memory, one structured `EpisodeMemory` per line (`id / date / time / location / participants / keywords / importance / content / memory_owner / title / raw_dialogue / last_recalled_at`), append-only, characters only; `id` is a stable UUID, `last_recalled_at` defaults to the event date and is refreshed from SQLite into saved archives, and old data can be backfilled with `scripts/backfill_episode_ids.py`
- `memory_draft.jsonl`: On-disk buffer for each round's `output.memory` (characters only), each line is `{"turn": int, "text": str}`; after consolidation's `EpisodeClosureDetector` determines closure, slices are read by `until_turn` to produce structured `EpisodeMemory` appended to `memory.jsonl`. Merged entries are removed from draft; unclosed turn entries are retained
- `understanding.jsonl`: Stable understandings formed by the character, one structured `Understanding` per line (`id / memory_owner / subject / keywords / content / linked_episodes / history`); unlike `EpisodeMemory`, this records durable beliefs or interaction patterns rather than single events. `history` belongs to that single Understanding and records the initial version plus content-changing updates as `{episode_id / date / title / content}`; link-only evidence updates do not append history entries
- `status.md`: Current status (prose fields only). Characters contain "心境 / 在意的事 / Relationship with Player"; narrator contains "场景 / 当前时间 / Character Locations / 叙事焦点 …". 打算 / 待触发事件 are no longer status.md sections — they live in `intents.json` / `pending_events.json` (see `intent_queue`). Narrator's "Relationship with Player" is not stored either — it is computed at prompt-build time from each character's own status (`- Character Display Name: Relationship`)

### History Files

- Current dialogue history is **only written** to `data/runtime/characters/narrator/raw/YYYY-MM-DD.jsonl`
- Each message carries `visible_to`
- When characters read context, they filter messages by visibility

### Other Runtime Files

- `data/runtime/characters/last_choices.json`: Latest set of player options, restored on load, cleared on reset
- `data/runtime/characters/.turn_counter.json`: Global narrator-turn counter, incremented by 1 for each narrator message; a turn starts with one narrator message and continues through character responses plus the next player input, until the following narrator message starts a new turn. Raw JSONL and `memory_draft.jsonl` entries carry turn numbers for `EpisodeClosureDetector` closure detection; reset clears with characters directory, and the opening narrator message writes the first turn
- `data/runtime/characters/narrator/world_schedule.json`: Narrator-owned world event calendar; `state_updater` reads `events[].status` to push pending public school/world events by date and story phase, and runtime marks triggered world events as `status="triggered"`; can be replaced wholesale by `world_schedule_update` when the story moves to a new environment
- `data/runtime/characters/narrator/tasks.md`: Optional story seed file; current main flow primarily syncs "Pending Events" from character "Intentions" via `state_updater`
- `data/runtime/characters/*/.history_window_state.json`: Per-agent dialogue history high/low water mark window sidecar
- `data/runtime/characters/*/.consolidation_state.json`: Character memory consolidation progress sidecar
- `data/runtime/characters/*/.memory_recall_state.json`: Legacy character long-term memory recall snapshot; new saves do not generate it, but old saves may still load it as a fallback when `memory.jsonl` lacks `last_recalled_at`

## Message Routing

The `narrator` decides who participates in the current round.

```text
User Input → narrator → targets: ["existing character name", ...] (LLMNarratorOutput.targets; may be empty if only introducing new characters this round)
```

### Narrator Responsibilities

- Analyze player input, output `targets` of currently existing characters who can respond this round; may be empty if only introducing new characters, the orchestration layer will add them after incubation
- Determine whether the player still wishes to interact with characters: if so, continue the current scene; if parting, skipping time, or no longer interacting, steer toward pending events or create equivalent immediate tension
- Every round must ensure at least one major character can perceive and respond to the player
- Describe time, location, present characters, environment, pure NPC behavior, and current hooks
- Do not add future events; future events are maintained by `state_updater` from character "Intentions"
- When the plot requires introducing a new character with relationship anchors, list `LLMNewCharacterRequest` anchors via `LLMNarratorOutput.new_characters` (`name_hint` is just an optional name hint); `app/character_factory.py` generates `character_id` and incubates the directory; the orchestration layer automatically adds successfully incubated new characters to this round's response list. Pure passersby are not generated, described directly in `present_characters` / `scene_description`
- **Never speak for characters or decide their actions**

## Single-Round Dialogue Flow

```text
User Message
  ↓
Invoke narrator, get LLMNarratorOutput (targets + date + time + location + present_characters + scene_description + new_characters)
  ↓
Incubate new_characters: `character_factory` generates `character_id`, writes soul/status/memory; successfully incubated new characters enter this round's final response list
  ↓
Write structured narrator output to single raw history (with visible_to)
  ↓
Sequentially invoke each target Agent (each agent response is written to history before the next can see it)
  ↓
After each Agent response: write back from LLMCharacterOutput typed fields, broadcast to history
  ↓
Launch three post-response lines together:
  1. choice generation → cancellable auxiliary task; if it finishes before the next player input, display 2-3 optional actions and persist them to `last_choices.json`
  2. state_updater → update narrator/status.md (narrative focus, pending events; "Relationship with Player" is not stored — it is computed at prompt-build time from each character's status)
  3. detect_and_consolidate(current_turn) → determine episode closure and merge memories (see "Memory Consolidation")
  ↓
Emit `response_done` so the UI can re-enable free input while those lines continue
  ↓
state_updater inputs in order: `characters_status` (深层目标/身份/心境/在意的事/打算 per character; 深层目标 read from each character's soul.md `<goal>`), `world_schedule`, latest_scene_json, current_narrator_status, recent_history
  ↓
narrator_service.route() writes 场景 / 当前时间 / 角色位置 synchronously to narrator/status.md before post-response tasks start; state_updater does not write these fields.
  ↓
state_updater reads the input blocks and decides on its own whether the upcoming plot needs new pending events (add_event) and what they are — no fixed sourcing taxonomy or per-round quota; the prompt states only the raw materials and the output contract. It still maintains a few mechanical fields: recent_world_event (a derived narrator status field prefixed with `（phase）`; "" keeps the old value), triggered_world_events (the pushed world-schedule event names, runtime marks them triggered), and world_schedule_update (full new calendar only on a major world change such as graduation / new environment).
  ↓
triggered prunes the narrator "待触发事件" queue (events that fired, were missed, or are duplicate); add_event names must keep the 【角色显示名：事件名】 prefix because triggered / mark_triggered match by that string
```

## Agent Output and Writeback Mechanism

All structured agents use pydantic-ai's `PromptedOutput` structured output, no longer using XML `<update_notes>`:

- `LLMCharacterOutput`: `content`, `memory`, `status`, `triggered`, `add_event`
- `LLMNarratorOutput`: `targets`, `date`, `time`, `location`, `present_characters`, `scene_description`, `new_characters` (routing, structured scene state, and dynamic character requests)
- `LLMNewCharacterRequest` / `LLMNewCharacterProfile`: New character incubation anchor (optional `name_hint`, no `character_id`) and character_factory's complete output (includes `character_id`, final `display_name`, `initial_status`)
- `LLMEpisodeMemory`: Single long-term memory event output by `EpisodeMemoryGenerator` (`date / time / location / participants / keywords / importance / content / title`), completed with a stable `id` by the write path and injected with `memory_owner` and `raw_dialogue` (original dialogue trace, metadata only, not vector-indexed, not in recall text), then appended to character `memory.jsonl`
- `EpisodeClosureDetector` output type: `dict[str, list[LLMEpisodeClosureBoundary]]` (key is the character's `agent_name` appearing in recent_history; value is all theme boundaries detected for that character in history, sorted by `end_turn` ascending, empty array means no boundaries. Each boundary contains `end_turn / old_theme / new_theme / reason`. Consumer only adopts local candidate characters, and takes the maximum `end_turn` from each array as this round's mergeable closure point)
- `LLMUnderstandingPatch`: add/update patch for stable `Understanding` records; entries contain `subject / keywords / content` (LLM does not output `linked_episodes`; the flow layer injects the current episode id automatically), while ids and owners are maintained by the flow layer
- `LLMStateUpdate`: `status`, `triggered`, `add_event` (post-round background narrator state maintenance)
- `LLMChoices`: `choices`

The runtime is split four ways (domain → repository → app → server): `models/Character` is a thin frozen-dataclass entity (`name` + read-only `soul`, derived `display_name`); `repository/character_repo.py` (`CharacterRepository`) and `repository/narrator_repo.py` (`NarratorRepository`) own all file I/O — soul caching, status read, and the writeback policy (`apply_status_fields` / `append_memory`; narrator's `write_scene` / world_schedule read-write); `app/conversation_service.py` (`ConversationService.run_turn`) and `app/narrator_service.py` (`NarratorService.route` / `update_state`) hold the orchestration. LLM DTOs are unpacked into primitives inside the Services before calling the repos, so `repository/` never imports `app/`. There is no global entity registry; Services obtain entities via `CharacterRepository.load(name)`, and `reset_entities()` (in `server.py`) invalidates the character soul cache on load / reset (narrator soul is read fresh, not cached). 打算 / 待触发事件 are typed lists in `repository/intent_queue.py` (`add` / `remove` / `render`), not status.md sections — the Services call `intent_queue.add(name, section, ...)` / `intent_queue.remove(...)` with the section constant (打算 / 待触发事件); the `无`-sentinel skip and【名】dedup live there. Because the queue is a real list, there is no "bulk-overwrite an event section" path to guard against. Narrator's `和玩家的关系` is no longer materialized — `prompt_builder.render_player_relations()` computes it at prompt-build time from each character's own status (single source of truth).

### Writeback Rules

- `output.memory` → append one record tagged with current narrator turn number to `memory_draft.jsonl` (after `EpisodeClosureDetector` determines closure turn, consolidation slices by `until_turn` to produce `EpisodeMemory` appended to `memory.jsonl`, merged entries removed from draft)
- `output.status` → overwrite corresponding prose fields in `status.md` (心境 / 在意的事 / 场景 …)
- `output.triggered` → `intent_queue.remove`: drop the matching `【名】` entry from the queue
- `output.add_event` → `intent_queue.add`: prepend a new entry (skips `无` sentinel and `【名】` duplicates)

Where:

- `narrator` operates the `待触发事件` queue (`pending_events.json`)
- Other characters operate the `打算` queue (`intents.json`)
- These queues are typed `list[str]` files, not status.md sections; they are rendered back into the prompt's `<status>` block by `build_status_block` so the LLM input format is unchanged. There is no bulk-overwrite path, so no guard is needed.

## Prompt Composition

### Design Principles

- Keep system prompts stable, put dynamic content in user messages to improve prompt cache hit rate
- Do not arbitrarily adjust context block order; current order is specifically tuned for cache and retrieval hit rates

### Character Agent

`system` message contains:

1. `soul.md`
2. `prompts.runtime_prompts.CHARACTER`
3. Allowlisted fields for writeback

`user` message is assembled into a **single large message** in the following order:

1. Recent visible dialogue history (built from raw JSONL; filtered by `visible_to`; high/low water mark truncation; history contains all narrator messages)
2. `status.md`
3. `<relevant_memories>` (long-term memory recall from `memory.jsonl`, vector store side still renders as markdown for LLM reading)
4. `<relevant_understandings>` (stable understanding recall from `understanding.jsonl`; relevance-only ranking, no recency or recall-state update)
5. Current round player input

### Narrator Agent

`system` message contains:

1. `soul.md`
2. `prompts.runtime_prompts.NARRATOR`

`user` message is assembled into a **single large message** in the following order:

1. Recent dialogue history (narrator keeps all recent messages in the active history window, including narrator messages)
2. `<fields>` (list of all active characters)
3. `status.md`
4. Current round player input

`narrator` does not use vector recall; it relies on scene, narrative focus, pending events (the `待触发事件` queue, rendered into `<status>`), and "Relationship with Player" to advance the current round. Pending events are primarily synced by `state_updater` from character "打算", event names preserve character names (e.g. `【Mitsuki: Promise to Walk Together】`). "Relationship with Player" is computed at render time from each character's status, format `- Mitsuki: Lover`.

> Note: `<world_now>` (derived projection of current time / real-time character locations) is currently disabled. During this period narrator only reads fields maintained by author/state_updater in `status.md`.

Narrator uses the main LLM configuration (`LLM_*` env vars).

### Choice Generation

After each participation round of character responses, `generate_choices()` is launched alongside `state_updater` and memory consolidation to generate 2-3 player-selectable actions. The player input box is already usable while choices are still generating. Starting a new `/api/chat` round invalidates and cancels any pending choice generation, clears stale saved choices, and prevents late results from writing `last_choices.json`. Observation rounds skip choice generation and clear `last_choices.json`.

- Prompt source: `prompts.runtime_prompts.CHOICES`
- Reuses the main `LLM_*` configuration
- Output style is player dialogue (may include parenthetical action descriptions), not action instructions
- Options are displayed as both text and buttons, persisted to `last_choices.json`

## Long-Term Memory Retrieval

- Vector store indexes long-term memory events from `memory.jsonl` and stable understandings from `understanding.jsonl` in separate tables; owner scope is fixed to current character
- Each turn retrieves both episode memories (`<relevant_memories>`) and stable understandings (`<relevant_understandings>`); `app/memory_query_builder.py` builds retrieval queries from the character's own `在意的事` status field (used only by the lexical/BM25 queries) and the current location (used only by the episode semantic query), extracted from the latest visible narrator message
- Retrieval query construction (`app/memory_query_builder.py` → `RetrievalQueries`): read character's `在意的事` from their own `status.md`; extract `location` from the most recent narrator message visible to that character; build four queries, each falls back to current player input if empty:
  - `episode` (semantic, limit 1800 chars): location + user_input (`在意的事` is kept out of the embedding query to avoid query drift; location anchors episode scene searches and the EpisodeMemory embedding index includes location)
  - `episode_bm25` (lexical, limit 700 chars): `在意的事` + user_input (location is not a useful BM25 signal)
  - `understanding` (semantic, limit 1200 chars): user_input only (understandings are not place-bound, and `在意的事` is kept out to avoid query drift)
  - `understanding_bm25` (lexical, limit 700 chars): `在意的事` + user_input
- `app/memory/retrieval.py` handles the full retrieval pipeline: semantic query embedding → vector candidates + optional BM25 lexical candidates → hybrid fusion → (optional) rerank → recency sort → recall state update
- `repository/vector_store.py` is storage layer only: provides raw candidates for EpisodeMemory and Understanding tables, pipeline logic is not here
- `app/memory/indexer.py` reads `EpisodeMemory` records from `memory.jsonl` and `Understanding` records from `understanding.jsonl`, then appends them to the vector store
- Recall ranking: vector relevance and BM25 relevance are fused first, rerank (optional) replaces relevance signal, then in-game time recency is layered on top
- When Logfire is configured, memory retrieval logs each round's semantic query, BM25 query, and top hit summary for debugging recall quality
- `last_recalled_at` is updated in SQLite on hit; save export merges the latest DB value into the archived `memory.jsonl`, while the working `memory.jsonl` remains append-only
- `app/memory/indexer.rebuild_memory_index()` reads `memory.jsonl.last_recalled_at` to restore the long-term memory index; when `clear_existing=True` (the default, used on load), it also rebuilds the Understanding index via the shared `_rebuild_understanding_index_for_agents` helper. Legacy `.memory_recall_state.json` is only a fallback for old saves whose `memory.jsonl` lacks `last_recalled_at`. `rebuild_understanding_index()` is still available as a standalone function for targeted rebuilds (e.g. after consolidation patches a single agent's understandings)

## Memory Consolidation

`app/consolidation/flow.py` handles character background consolidation:

- Triggered as a background task by `detect_and_consolidate(current_turn)` at the end of each round (concurrent with `state_updater`): first scans characters with `memory_draft.jsonl` as candidates, calls `EpisodeClosureDetector` to determine which characters have closed episodes this round (returns `{agent_name: closed_at_turn}`); closed characters execute `consolidate_agent(name, until_turn=closed_at_turn)` in parallel
- `consolidate_agent` slices draft entries + raw dialogue for the corresponding turn range from `memory_draft.jsonl` by `until_turn`, hands to `EpisodeMemoryGenerator` to produce a single structured `EpisodeMemory`; flow layer injects `memory_owner` and `raw_dialogue`, then appends to `memory.jsonl`. Merged entries are removed from draft; unclosed turn entries are retained. On failure, entire draft is retained for retry next round
- Patches `understanding.jsonl` from the new `EpisodeMemory`, preserving existing linked episode ids, initializing per-Understanding `history` on add and appending to it when `content` changes, and syncing changed Understanding records to the vector store
- Syncs vector index by progress

`narrator` does not maintain `memory.jsonl`, nor does it participate in consolidation.

## Configuration Sources

### `.env`

- Holds secrets, model IDs, and external service URLs
- `EMBEDDING_DIM=auto` or an empty value means the embedding client omits `dimensions` and the vector store creates sqlite-vec tables from the first real embedding length; numeric values are only for embedding services that support the `dimensions` parameter
- Rerank calls are only truly enabled when `RERANK_MODEL` is configured
- Generated dialogue, background generation, and choice generation all use the main `LLM_*` configuration

### `config.toml`

- Holds runtime strategy parameters, such as Agent temperature, LLM retry attempts, character/consolidation/choice generation timeouts, embedding/rerank request timeouts, vector retrieval weights
- `[history]`'s `history_high` / `history_low` control multi-round dialogue high/low water mark truncation (in distinct turn counts, anchored by turn number, stored in `.history_window_state.json`'s `start_turn` field); `raw_scan_turns` limits how many turns narrator routing / character running / choice generation reads from raw history (consolidation flow slices by turn, not subject to this limit)

## Save and Reset

Handled by `repository/save_manager.py`, exposed via FastAPI endpoints:

- `POST /api/save`: Export a new immutable worldline node zip to `saves/`; filename-based overwrite is rejected
- `GET /api/saves`: List saves and return temporary worldline trees grouped by `story_id`
- `DELETE /api/save/{filename}`: Delete the Game tree rooted at the given save
- `DELETE /api/save-node/{filename}`: Delete one save only when it has no child branches
- `POST /api/load`: Restore save and rebuild necessary indexes
- `POST /api/reset`: Reset runtime data from `data/templates/{story_id}`

Save includes:

- `metadata.json` (save metadata: `save_id`, `parent_save_id`, `story_id`, `created_at`, `turn`, `title`, `focus`; this is the only persisted branch relationship source)
- `.save_id` (current save node id restored on load; reset clears it so the first save is a root node)
- Character markdown / jsonl files (`narrator` does not include `memory.jsonl` / `memory_draft.jsonl`)
- Character `memory.jsonl` (structured long-term memory, one `EpisodeMemory` per line, includes stable `id`, `raw_dialogue` trace field, and archive-refreshed `last_recalled_at`)
- Character `memory_draft.jsonl` (when present; each line `{"turn": int, "text": str}`, ensuring unclosed merge memories are not lost on save)
- Character `understanding.jsonl` (when present; stable beliefs linked back to EpisodeMemory ids)
- Character `intents.json` / narrator `pending_events.json` (when present; the 打算 / 待触发事件 typed-list queues)
- Narrator raw history (each entry carries turn number)
- Narrator `world_schedule.json` when present
- Per-agent `.history_window_state.json`
- Character `.consolidation_state.json`
- `last_choices.json`
- `.turn_counter.json` (global turn counter)

Currently built-in story templates:

- `drama`: `linxi` / `shenzhiyi` / `narrator`

## Development Conventions

### Code Design

- Keep DRY, but don't abstract for abstraction's sake
- Prioritize simple, explicit, good-enough-for-now implementations
- One function does one thing, keep complexity controlled
- Complete type annotations (Python 3.11+)

### Error Handling

- LLM / embedding / database calls must retain contextual logs
- Check paths and existence before file operations
- Prohibit bare `except:`, catch specific exceptions

### Concurrency and Async

- All I/O operations use `async/await`
- Multi-character invocations use `asyncio.gather()` for parallel execution
- Consider concurrency protection for shared resources (files, vector store, consolidation tasks)

### Readability

- Variable and function names should be self-explanatory
- Comments explain "why", not restate surface meaning of code
- Sync documentation and prompts when structure changes

## Logging and Observability

- When Logfire is configured (local CLI or `LOGFIRE_TOKEN`), reports PydanticAI traces, token/cost, routing events, memory retrieval and consolidation events; silently skipped when not configured
- Routing and memory modules still use standard logger as business event entry points, but no longer write to local `logs/*.log` rotation files by default

## Testing Conventions

- Keep pure logic as unit-testable functions
- Use `pytest`
- Current tests include:
  - Dialogue history related tests
  - Formatting tests
  - Save consistency tests
  - Vector store tests
- Tests involving vector retrieval/embedding may depend on embedding configuration in `.env`
