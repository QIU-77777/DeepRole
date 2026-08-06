# Benchmarks

针对 DeepRole 记忆与检索链路的三组可复现实验，用于量化「落地与验证」：

| 脚本 | 测什么 | 外部依赖 |
|---|---|---|
| `exp1_retrieval.py` | 四管线召回质量（纯向量 / 纯 BM25 / hybrid / hybrid+rerank） | 真实 embedding + rerank API |
| `exp2_tokens.py` | Token 占用曲线（全量注入 vs 混合召回窗口） | 无（纯离线） |
| `exp3_consolidation.py` | consolidation 压缩比与耗时 | 真实 LLM（DeepSeek-V4-Flash） |

结果输出到 `bench/results/`。

## 运行前置

- conda 环境（见 README 根目录快速开始），`pydantic-ai==1.79.0`
- `exp1` / `exp3` 需要项目根目录 `.env` 配置了 SiliconFlow 的 key
- `exp1` 用独立向量库（`results/exp1_vectors.sqlite`），不碰正式 `data/runtime/vectors.sqlite`
- `exp3` 运行前后会自动备份 / 恢复 `data/runtime`，**不要在重要存档进行中运行**

```bash
python bench/exp2_tokens.py        # 离线，秒级
python bench/exp1_retrieval.py     # ~10s，烧少量 embedding/rerank
python bench/exp3_consolidation.py # ~1min，烧少量 LLM token
```

## 结果与方法论

### exp1 — 召回质量（主题级）

设计：6 个职场主题 × 8 条「同主题纠缠」记忆（语义接近、仅细节不同），
18 条 query 分三类——exact（精确词面）/ spoken（口语改述）/ confusable（易混淆）。
ground-truth 取「主题全集」（同主题 8 条任一命中即算相关）：
记忆系统的目标是「回忆起有过相关经历」，而非精确定位某一条细节。

合成集实测（主题级 R@1 / MRR / spoken-R@1）：

| pipeline | R@1 | R@3 | R@5 | MRR | spoken-R@1 |
|---|---|---|---|---|---|
| 纯向量 | 1.000 | 1.000 | 1.000 | 1.000 | 1.00 |
| 纯 BM25 | 0.833 | 0.889 | 0.889 | 0.861 | 0.50 |
| hybrid 融合 | 0.889 | 1.000 | 1.000 | 0.944 | 0.67 |
| hybrid + rerank | 0.889 | 1.000 | 1.000 | 0.944 | 0.83 |

读法（如实）：

- 纯 BM25 在「口语改述」query 上掉到 0.50（改述后词面对不上）；
  hybrid 把它补回 0.67，rerank 再提到 0.83 —— 证明混合召回 + 精排
  链路救回了单路词面检索漏掉的记忆。
- **边界**：合成记忆语义可区分度偏高，纯向量在主题级口径接近满分；
  「hybrid 显著优于纯向量」这个论断在本口径下不成立。
  要验证该论断需更接近真实分布的长尾记忆集。
- R@3 全部 ≥0.889：即使 rank 有偏差，目标主题也基本进入前 3。

### exp2 — Token 占用曲线

合成 60 轮对话，对比两套输入策略每轮的 LLM context 规模：

| 轮次 | 全量注入 (tok) | DeepRole (tok) | 下降 |
|---|---|---|---|
| 10 | 978 | 854 | +12.7% |
| 20 | 1913 | 942 | 50.8% |
| 40 | 3784 | 1030 | 72.8% |
| 60 | 5655 | 1030 | 81.8% |

读法：

- 基线随轮次线性增长；DeepRole 平台化（约 1000 token 封顶）。
- 这是结构性结论：由 `config.toml` 的 `history_low=8` 窗口 + `episode_search_limit=3`
  召回直接决定，不依赖任何人为标注，复现稳定。
- **边界**：token 数为保守估算（中文 1 字符/个、英文 4 字符/个），
  且「召回 3 条记忆」用合成记忆块近似，未计入真实检索的召回质量差异。
- 前 5 轮为负：记忆块固定开销大于省下的历史——长对话优势才显现。

### exp3 — consolidation 压缩比

真实 LLM 跑一次 16 轮剧情弧（设计稿延期事件闭环）：

- raw 对话 286 字符 → EpisodeMemory.content 71 字符，**压缩约 4.0x**
- consolidation 耗时约 69s（含 EpisodeMemory 生成 + understanding patch）
- 同步产出 1 条 Understanding（「我和他的工作配合方式」）

**边界**：单样本；raw 仅计消息正文，未计入旁白细节与 raw_dialogue 全文
（`raw_dialogue` 仍作为可回溯 metadata 保留在 EpisodeMemory 里）。

## 局限

- exp1/exp2 的对话与记忆为合成数据，非真实用户长弧；
- 样本量小（exp1=18 query，exp3=1 条弧），结果反映量级而非严格统计显著；
- 如需更强的「hybrid 优于纯向量」证据，下一步应基于真实对话长弧
  做 human-judged 的记忆召回集。
