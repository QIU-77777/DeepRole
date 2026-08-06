"""实验 A：召回质量对比（纯向量 / 纯 BM25 / hybrid / hybrid+rerank）。

设计目标：制造管线间的真实分歧，而非"主题完全分离导致全员满分"。

数据构造：
- 6 个主题，每主题 8 条记忆。同主题内记忆语义高度接近（都含主题词），
  仅靠 1 个"触发细节"（日期 / 专有名词 / 数字 / 人物）区分——模拟真实记忆库
  里"同一件事反复出现、细节各异"的纠缠状态。
- 每主题 3 类 query，对应不同管线的优势场：
    exact      精确词面型：query 含触发细节词 → BM25 应精准命中
    spoken     口语语义型：query 改述不含细节词 → 向量靠语义区分，BM25 在同主题乱猜
    confusable 易混淆型：query 表面词多条共享，正确条目靠细节精排 → rerank 主场

metrics：主题级 Recall@1/@3/@5 + MRR（同主题 8 条均算相关——记忆系统目标是
"回忆起有过相关经历"，而非精确定位某一条）。运行需真实 embedding（bge-m3）
与 rerank（bge-reranker-v2-m3），读取项目根目录 .env。

用法：
    python bench/exp1_retrieval.py
  输出：bench/results/exp1_retrieval.json
"""
import os
import sys
import json
import asyncio

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

import sqlite3
from models import EpisodeMemory
from repository.llm.embedding import embed_async
from repository.vector_store import vector_store, VectorStore
from repository import vector_store as vs_mod
import app.memory.retrieval as rt

RESULTS_DIR = os.path.join(PROJECT_ROOT, "bench", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

# 实验用独立 DB，不污染正式向量库
_EXP_DB = os.path.join(RESULTS_DIR, "exp1_vectors.sqlite")
vs_mod.DB_PATH = _EXP_DB
rt.DB_PATH = _EXP_DB
if os.path.exists(_EXP_DB):
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(_EXP_DB + suffix)
        except OSError:
            pass
GROUP = "bench_a"

_THEMES = {
    "设计稿延期": {
        "word": "设计稿延期",
        "memories": [
            {"detail": "交互走查", "text": "设计稿因交互走查返工三次才通过，交付因此推迟到周四"},
            {"detail": "字体版权", "text": "设计稿因为字体版权没授权被法务打回，只好重新排版重画"},
            {"detail": "动画效果", "text": "设计稿里的动画效果被需求方反复改动，最终交付晚了两天"},
            {"detail": "视觉规范", "text": "新版视觉规范临时升级，设计稿需要全部重排，排期顺延"},
            {"detail": "人手不足", "text": "设计组人手不够，设计稿交付排不过来，只好拆期交付"},
            {"detail": "需求变更", "text": "需求临时变更，设计稿随之推翻重来，交付时间一起后移"},
            {"detail": "环境问题", "text": "设计稿交付前遇到字体环境问题，重新导出才解决"},
            {"detail": "走查不通过", "text": "设计稿被交互走查卡住多轮，提交迟迟没法进入评审"},
        ],
        "queries": [
            {"type": "exact", "text": "上次设计稿因为字体版权的事延期了对吗？"},
            {"type": "spoken", "text": "那个稿子怎么又拖了？"},
            {"type": "confusable", "text": "设计稿延期到底是什么情况？"},
        ],
    },
    "技术选型": {
        "word": "技术选型",
        "memories": [
            {"detail": "Postgres", "text": "数据库技术选型最终定了 Postgres，团队里评估过 MySQL"},
            {"detail": "SQLite", "text": "本地存储讨论过 SQLite，因为数据量小可以应付原型"},
            {"detail": "MongoDB", "text": "文档型数据库 MongoDB 作为备选，后来没有采用"},
            {"detail": "Redis", "text": "缓存方案定了 Redis，用于热点数据与限流"},
            {"detail": "搜索引擎", "text": "搜索引擎选型悬而未决，暂时先用数据库模糊查询顶着"},
            {"detail": "FastAPI", "text": "服务端框架用了 FastAPI，异步支持比较顺手"},
            {"detail": "消息队列", "text": "消息队列还没有定下来，异步任务先同步跑"},
            {"detail": "对象存储", "text": "文件存储讨论过对象存储，图片量大需要单独服务"},
        ],
        "queries": [
            {"type": "exact", "text": "数据库最后用的是 Postgres 还是别的？"},
            {"type": "spoken", "text": "后端存储那事到底定下来没？"},
            {"type": "confusable", "text": "技术选型里缓存是怎么定的？"},
        ],
    },
    "预算申请": {
        "word": "预算",
        "memories": [
            {"detail": "服务器", "text": "预算申请里服务器扩容的费用被财务驳回了"},
            {"detail": "工具授权", "text": "预算里设计工具授权的申请暂时没有批"},
            {"detail": "人力", "text": "预算会议只批了人力成本，新增资源没有批"},
            {"detail": "差旅", "text": "异地办公的差旅预算申请提交后还在审批中"},
            {"detail": "招聘", "text": "招聘预算被压缩，扩编的计划顺延到下一个季度"},
            {"detail": "采购", "text": "测试设备采购的预算申请被打回，要求重新报价"},
            {"detail": "灰度资源", "text": "灰度环境资源的预算批下来了，额度不算大"},
            {"detail": "营销投放", "text": "营销投放的预算被砍，市场说活动缩水"},
        ],
        "queries": [
            {"type": "exact", "text": "服务器扩容那笔预算最后批了吗？"},
            {"type": "spoken", "text": "这季度钱的事怎么说？"},
            {"type": "confusable", "text": "预算申请里有哪些被驳回了？"},
        ],
    },
    "需求冲突": {
        "word": "需求",
        "memories": [
            {"detail": "接口拆分", "text": "研发认为需求范围太大，接口要拆成多期才能落地"},
            {"detail": "性能", "text": "研发说这个动画需求性能扛不住，不同意直接上"},
            {"detail": "边界", "text": "需求边界没写清楚，研发拒绝排期要求先补充文档"},
            {"detail": "评审流程", "text": "研发坚持需求必须先过评审再开发，不能直接塞"},
            {"detail": "异常场景", "text": "研发让补需求文档里的异常场景，否则不接"},
            {"detail": "排期", "text": "研发说排期紧张，需求只能安排到下个迭代"},
            {"detail": "优先级", "text": "研发质疑需求优先级，说当前任务更重要"},
            {"detail": "deadline", "text": "研发认为 deadline 太紧，按当前人力做不完"},
        ],
        "queries": [
            {"type": "exact", "text": "上次需求是不是因为接口拆期的事被拒的？"},
            {"type": "spoken", "text": "研发为啥不肯接这个需求？"},
            {"type": "confusable", "text": "需求冲突主要是卡在哪？"},
        ],
    },
    "用户反馈": {
        "word": "用户反馈",
        "memories": [
            {"detail": "首屏加载", "text": "灰度用户反馈首屏加载太慢，希望优先优化"},
            {"detail": "视觉太亮", "text": "用户集中反馈新版视觉太亮，阅读时很疲劳"},
            {"detail": "入口深", "text": "灰度用户反馈功能入口太深，找不到路径"},
            {"detail": "登录态", "text": "有用户反馈登录态会失效，需要重新登录"},
            {"detail": "夜间模式", "text": "部分用户建议增加夜间模式，弱光下阅读"},
            {"detail": "字号", "text": "灰度群里有人反馈字号太小，看着费劲"},
            {"detail": "动效", "text": "用户觉得页面动效太多，切换时晕"},
            {"detail": "注册流程", "text": "反馈提到注册流程太繁琐，流失率高"},
        ],
        "queries": [
            {"type": "exact", "text": "灰度反馈里首屏加载慢被提得最多吗？"},
            {"type": "spoken", "text": "用户对新版本有啥不满？"},
            {"type": "confusable", "text": "灰度用户都反馈了些什么问题？"},
        ],
    },
    "排期协调": {
        "word": "排期",
        "memories": [
            {"detail": "发布会", "text": "发布会物料在等设计稿，排期因此卡得很紧"},
            {"detail": "评审会", "text": "评审会安排在周四，前面所有产出都要提前冻结"},
            {"detail": "联调", "text": "前后端联调时间被压缩，排期里没留缓冲"},
            {"detail": "走查", "text": "交互走查安排在发版前一周，返工就会顶掉缓冲"},
            {"detail": "上线窗口", "text": "上线窗口只在周末，错过就要再等一周"},
            {"detail": "灰度节奏", "text": "灰度分两批放量，节奏由排期表决定"},
            {"detail": "版本冻结", "text": "版本冻结后只允许修 bug，新需求进不来"},
            {"detail": "回滚窗口", "text": "回滚窗口很短，发布风险要提前评估"},
        ],
        "queries": [
            {"type": "exact", "text": "排期里发布会物料等设计那档卡在哪？"},
            {"type": "spoken", "text": "这轮交付为啥这么赶？"},
            {"type": "confusable", "text": "排期上有什么风险点？"},
        ],
    },
}


def build_episodes():
    eps = []
    ti = 0
    for topic, body in _THEMES.items():
        for k, mem in enumerate(body["memories"]):
            eps.append(EpisodeMemory(
                id=f"t{ti}_{k}", date="1月1日", time="上午",
                location="会议室",
                participants="陆时川、江知夏",
                keywords=[body["word"], mem["detail"]],
                importance=3, content=mem["text"], memory_owner=GROUP,
                title=f"{topic}#{mem['detail']}",
            ))
        ti += 1
    return eps


async def seed(eps):
    return await vector_store.add_episodes(eps)


def build_rowid_map(conn):
    m = {}
    for (rowid, title) in conn.execute(
        "SELECT rowid, title FROM EpisodeMemory WHERE memory_owner=?", (GROUP,)
    ).fetchall():
        m[title] = rowid
    return m


def _ids(ranked):
    return [int(r["id"]) for r in ranked]


def run_vector(conn, qvec, limit):
    rows = vs_mod.vector_store.get_vector_candidates(
        conn, GROUP, qvec, rt.VECTOR_CANDIDATE_LIMIT
    )
    return _ids(rt.apply_recency(rt._vec_rows_to_candidates(rows), None))[:limit]


def run_bm25(conn, bm25q, limit):
    rows = vs_mod.vector_store.get_bm25_candidates(
        conn, GROUP, bm25q, rt.BM25_CANDIDATE_LIMIT
    )
    docs = [
        {"id": str(int(r[0])), "content": r[1], "relevance": -float(r[2]),
         "date": "", "last_recalled_at": "", "importance": 3}
        for r in rows
    ]
    return _ids(rt.apply_recency(docs, None))[:limit]


def run_hybrid(conn, qvec, bm25q, limit):
    vrows = vs_mod.vector_store.get_vector_candidates(
        conn, GROUP, qvec, rt.VECTOR_CANDIDATE_LIMIT
    )
    brows = vs_mod.vector_store.get_bm25_candidates(
        conn, GROUP, bm25q, rt.BM25_CANDIDATE_LIMIT
    )
    cands = rt.hybrid_fusion(vrows, brows) if brows else rt._vec_rows_to_candidates(vrows)
    return _ids(rt.apply_recency(cands, None))[:limit]


def run_hybrid_rerank(conn, q, qvec, bm25q, limit):
    vrows = vs_mod.vector_store.get_vector_candidates(
        conn, GROUP, qvec, rt.VECTOR_CANDIDATE_LIMIT
    )
    brows = vs_mod.vector_store.get_bm25_candidates(
        conn, GROUP, bm25q, rt.BM25_CANDIDATE_LIMIT
    )
    cands = rt.hybrid_fusion(vrows, brows) if brows else rt._vec_rows_to_candidates(vrows)
    cands, applied = rt._try_rerank(q, cands, GROUP)
    if not applied:
        print(f"  [warn] rerank 降级（未应用）: {q}")
    return _ids(rt.apply_recency(cands, None))[:limit]


def hit_at(ids, gt, k):
    return any(x in gt for x in ids[:k])


def reciprocal(ids, gt):
    for i, x in enumerate(ids, 1):
        if x in gt:
            return 1.0 / i
    return 0.0


async def main():
    eps = build_episodes()
    n = await seed(eps)
    print(f"seeded {n} episodes ({len(_THEMES)} themes x 8)")

    conn = sqlite3.connect(_EXP_DB)
    VectorStore._load_sqlite_vec_sync(conn)
    rowid_map = build_rowid_map(conn)

    # ground-truth 改为「主题全集」：同主题 8 条全算相关。
    queries = []
    for topic, body in _THEMES.items():
        target_titles = [f"{topic}#{m['detail']}" for m in body["memories"]]
        full_gt = [rowid_map[t] for t in target_titles]
        for q in body["queries"]:
            queries.append((q["type"], q["text"], full_gt))

    texts = [q for _, q, _ in queries]
    qvecs = await embed_async(texts)

    names = ["vector", "bm25", "hybrid", "hybrid+rerank"]
    per_type = {}
    for nm in names:
        per_type[nm] = {"exact": [], "spoken": [], "confusable": []}
    overall = {nm: {"r1": [], "r3": [], "r5": [], "mrr": []} for nm in names}

    for (qtype, q, gt), qv in zip(queries, qvecs):
        plans = [
            ("vector", run_vector(conn, qv, 5)),
            ("bm25", run_bm25(conn, q, 5)),
            ("hybrid", run_hybrid(conn, qv, q, 5)),
            ("hybrid+rerank", run_hybrid_rerank(conn, q, qv, q, 5)),
        ]
        for name, ids in plans:
            r = overall[name]
            r["r1"].append(hit_at(ids, gt, 1))
            r["r3"].append(hit_at(ids, gt, 3))
            r["r5"].append(hit_at(ids, gt, 5))
            r["mrr"].append(reciprocal(ids, gt))
            per_type[name][qtype].append(hit_at(ids, gt, 1))

    print("\n=== 实验 A：主题级召回质量（6 主题 × 18 query，同主题 8 条均算相关）===")
    print(f"{'pipeline':<14} {'R@1':>6} {'R@3':>6} {'R@5':>6} {'MRR':>7}   "
          f"R@1分组(exact/spoken/confusable)")
    rows = []
    for name in names:
        r = overall[name]
        nq = len(r["r1"])
        r1, r3, r5 = sum(r["r1"])/nq, sum(r["r3"])/nq, sum(r["r5"])/nq
        mrr = sum(r["mrr"])/nq
        e = sum(per_type[name]["exact"])/len(per_type[name]["exact"])
        s = sum(per_type[name]["spoken"])/len(per_type[name]["spoken"])
        c = sum(per_type[name]["confusable"])/len(per_type[name]["confusable"])
        print(f"{name:<14} {r1:6.3f} {r3:6.3f} {r5:6.3f} {mrr:7.3f}   "
              f"{e:.2f} / {s:.2f} / {c:.2f}")
        rows.append({
            "pipeline": name, "r@1": r1, "r@3": r3, "r@5": r5, "mrr": mrr,
            "r1_exact": e, "r1_spoken": s, "r1_confusable": c,
        })

    out = os.path.join(RESULTS_DIR, "exp1_retrieval.json")
    with open(out, "w") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"saved -> {out}")

    await vector_store.close()


if __name__ == "__main__":
    asyncio.run(main())