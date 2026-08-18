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

`src/maps/graybox.json` 使用与 Tiled 对象层相近的结构，暂时作为四张地图的占位数据。正式地图接入时保持 `map_id`、出口 `id`、waypoint 和交互对象 ID 稳定，再替换为 Tiled JSON 导出物。
