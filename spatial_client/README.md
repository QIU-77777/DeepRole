# 空间客户端

这是 DeepRole 的并行空间化客户端，使用 Vue 3、TypeScript、Phaser 3 和 Vite。

## 本地开发

```bash
npm install
npm run dev
```

Vite 开发服务器默认运行在 `http://localhost:5173/game/`，并将 `/api` 代理到本地 FastAPI (`http://127.0.0.1:8000`)。

## 构建并由 FastAPI 提供

```bash
npm run build
```

构建产物写入 `spatial_client/dist/`，FastAPI 的 `/game` 入口会在产物存在时提供它。`node_modules/` 和 `dist/` 不入库。

## 灰盒地图

`src/maps/tiled/*.json` 是当前运行时使用的 Tiled JSON 地图源，包含 `walls`、`exits`、`waypoints` 和 `npcs` 对象层；`src/maps/graybox.json` 保留为同构灰盒参考。`src/maps/loader.ts` 负责单张导出物归一化和 tile 尺寸校验。正式美术替换时保持 `map_id`、出口 `id`、waypoint 和交互对象 ID 稳定。主要 NPC 的位置由后端返回的语义 waypoint 投影到地图，客户端不接受坐标式 NPC 传送；同场景 waypoint 更新会经过碰撞矩形上的八方向逐格路径动画。

地图中的 `kind: "ambient"` NPC 只显示预写环境气泡，不进入 AI 对话流；未标记的 NPC 默认按主要角色处理。
