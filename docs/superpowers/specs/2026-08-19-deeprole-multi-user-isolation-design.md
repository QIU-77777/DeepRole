# DeepRole 多用户数据隔离 — 设计文档

日期：2026-08-19
状态：已确认（用户已审阅方案 1）

## 背景与目标

DeepRole 当前是单世界应用：所有访问者共享 `data/runtime/` 下的同一份角色、对话、记忆、存档，无任何用户身份机制（无 session/token/cookie）。现要对外开放，需要**每用户完全独立的世界数据**，互不可见、互不影响。

已确认的决策：
- 身份机制：**免注册匿名 token**（客户端生成 UUID 由服务器签发，localStorage 持久化）
- 隔离粒度：每用户一份独立世界（数据 + 向量库 + 内存态）
- 防滥用：**暂不限流**
- 部署形态：**单进程 + contextvar 用户上下文**（uvicorn 单进程，不拆多 worker）

## 1. 身份与 Token

- 首次请求（请求头无 `X-User-Token`）→ 服务器生成 `uuid4` token 与 `user_id`，写入 `users.db`，在响应头/响应体返回；前端存 `localStorage`，后续请求携带。
- FastAPI 依赖层解析 `X-User-Token` → 查 `users.db` → 设置 `contextvar(current_user_id)`。
- 新增全局数据库 `data/users.db`（与 `data/runtime/{user_id}/` 分开，不隔离）：
  - 表 `users(token TEXT PRIMARY KEY, user_id TEXT UNIQUE, created_at TEXT)`

## 2. 数据目录隔离

```
data/
├── users.db                       # 全局：token → user 映射
├── templates/                     # 全局：只读剧本模板（保持现状）
└── runtime/{user_id}/             # 每用户独立世界
    ├── characters/…               # 角色 soul/status/memory/对话/存档
    └── vectors.sqlite             # 每用户独立向量库
```

改造集中点（`repository/config.py`）：
- `RUNTIME_DIR` / `CHARACTERS_DIR`：由模块常量改为从 contextvar 派生 user_id 的函数
- `character_path()`：返回 `data/runtime/{user_id}/characters/...`
- `get_agent_names()`：扫描当前用户的 characters 目录

`repository/vector_store.py`：
- 模块常量 `DB_PATH`（import 时固化）改为按 user 解析
- 全局单例 `vector_store = VectorStore()` 改为 per-user 懒加载缓存

`repository/save_manager.py`：`TEMPLATES_DIR` 保持全局（只读模板），其余路径走 `character_path()` 自动隔离。

## 3. 内存态隔离

| 全局状态 | 位置 | 改造 |
| --- | --- | --- |
| `_conversation_agents`（角色 agent 注册表） | `app/agent_factory.py` | → `dict[user_id, dict[角色名, Agent]]` |
| `_choices_agent` / `_state_updater_agent` / `_character_factory_agent` / `_consolidation_agents` | `app/agent_factory.py` | 指令固定、无用户数据，**保持全局共享** |
| `_pending_choices_task` / `_choices_generation_token` | `server.py` | → `dict[user_id, ...]` |
| `_pending_state_update_task` / `_pending_state_update_requested` | `server.py` | → `dict[user_id, ...]` |
| `narrator_service` / `message_router` 等模块级缓存 | app/repository | 逐一审查，凡缓存用户相关数据者按 user 隔离 |

原则：指令固定、不读取用户数据的单例对象共享；凡是按角色/世界构建、会随用户变化的对象一律 per-user。

## 4. 新用户初始化

- 依赖层识别到新 `user_id` 时：创建 `data/runtime/{user_id}/`，调用现有 `bootstrap_new_characters()`（从 `data/templates` 初始化角色），复用现有逻辑不重写。
- 服务器现有 `data/runtime`（仅初始剧本、无玩家数据）弃用。

## 5. 前端改动

- `static/app.js`：请求封装统一注入 `X-User-Token`；首次无 token 时先调用 `/api/me`（或等价接口）获取并持久化到 `localStorage`。
- 覆盖全部 `fetch` 调用点（`fetchJson` 及裸 `fetch`）。

## 6. 并发与安全

- 保持单进程 uvicorn（避免 state update loop 重复执行）。多 worker 不在本设计范围内。
- 匿名 token 泄露即被冒用（匿名方案固有局限），不在本设计解决。
- 安全约束：`user_id` 仅由服务器生成（uuid4），不信任任何客户端提供的路径片段，杜绝路径穿越。

## 7. 测试

- repository 层：不同 contextvar user 下 `character_path()` 返回不同目录；各自读写在隔离目录内。
- API 层：两个 token 各自请求 `/api/init` 得到独立世界；A 的对话/存档对 B 不可见。
- 回归：现有 42 项测试（含 layer-dependency AST 测试）全部通过。

## 8. 非目标（YAGNI）

- 不做注册/登录/密码体系
- 不做每用户限流
- 不做跨设备数据同步（换设备 token 即丢失）
- 不做管理后台/用户列表

## 9. 部署

- 改动完成后推送到 GitHub `feat/multi-user-isolation` → 合并 main
- 服务器 `/var/www/DeepRole` `git pull` 更新，`systemctl restart deeprole`
- `data/users.db` 与 `data/runtime/*` 归运行 deeprole 服务的用户（当前为 root）可读写，注意 `.gitignore` 已排除 `data/`
