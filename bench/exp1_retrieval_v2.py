"""实验 A v2：基于真实剧本的混合检索召回质量验证（独立版）。

设计：
- 24 条记忆，每条短而独特，聚焦单一事实
- 制造"关键词陷阱"：trap 与 target 共享关键词但描述不同事件
- query 分 4 类：exact / spoken / cross-event / emotional
- 使用 RRF（Reciprocal Rank Fusion）做混合融合

用法：
    /usr/local/bin/python3 bench/exp1_retrieval_v2.py
"""
import os, sys, json, sqlite3, asyncio, math, array, time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

import httpx
from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

RESULTS_DIR = os.path.join(PROJECT_ROOT, "bench", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

EMBEDDING_API_URL = os.environ["EMBEDDING_API_URL"]
EMBEDDING_MODEL = os.environ["EMBEDDING_MODEL"]
EMBEDDING_API_KEY = os.environ["EMBEDDING_API_KEY"]
RERANK_API_URL = os.environ.get("RERANK_API_URL", "")
RERANK_MODEL = os.environ.get("RERANK_MODEL", "")
RERANK_API_KEY = os.environ.get("RERANK_API_KEY", "")

_EXP_DB = os.path.join(RESULTS_DIR, "exp1_v2_vectors.sqlite")
for suffix in ("", "-wal", "-shm"):
    try: os.remove(_EXP_DB + suffix)
    except OSError: pass

GROUP = "bench_v2"


async def embed_texts(texts):
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(EMBEDDING_API_URL,
            headers={"Authorization": f"Bearer {EMBEDDING_API_KEY}", "Content-Type": "application/json"},
            json={"model": EMBEDDING_MODEL, "input": texts})
        resp.raise_for_status()
        return [item["embedding"] for item in resp.json()["data"]]


async def rerank_texts(query, documents):
    if not RERANK_MODEL or not RERANK_API_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(RERANK_API_URL,
                headers={"Authorization": f"Bearer {RERANK_API_KEY}", "Content-Type": "application/json"},
                json={"model": RERANK_MODEL, "query": query, "documents": documents})
            resp.raise_for_status()
            data = resp.json()
            results = sorted(data["results"], key=lambda x: x["index"])
            return [r["relevance_score"] for r in results]
    except Exception as e:
        print(f"  [warn] rerank error: {type(e).__name__}: {e}")
        return None


def embed_texts_sync(texts):
    return asyncio.run(embed_texts(texts))


def rerank_texts_sync(query, documents):
    return asyncio.run(rerank_texts(query, documents))


# ---------------------------------------------------------------------------
# 24 条记忆：短而独特 + 关键词陷阱
# 陷阱设计：trap 与 target 共享关键词但描述不同事件/场景
# ---------------------------------------------------------------------------

MEMORIES = [
    # --- 场景一：初见（10月2日）---
    ("m01", "10月2日", "上午", "茶水间",
     "帮她按了咖啡机按钮，她递纸杯时说了句'勉强算你前辈'。"),
    ("m02", "10月2日", "上午", "茶水间",
     "周末在茶水间遇到她，她说'这机器总卡'。我帮她按了按钮，她谢了我。"),  # trap: 同地点+同动作
    ("m03", "10月2日", "上午", "走廊",
     "故意让杯沿碰了碰他的，问他紧不紧张。"),
    ("m04", "10月2日", "上午", "走廊",
     "本想说句贴心话，耳根却热了。只好撂下'趁热喝别烫着'，走出两步又回头。"),

    # --- 场景二：评审（10月3日）---
    ("m05", "10月3日", "傍晚", "会议室",
     "散场后众人皆去，我借口帮他收拾图纸多待了一会儿。"),
    ("m06", "10月3日", "傍晚", "会议室",
     "我是最后走的。行至门口瞥见她还窝在椅中帮新人归整材料。"),
    ("m07", "10月3日", "傍晚", "会议室",
     "众人散尽，她凑过来帮忙收拾。发丝垂落，我伸手替她拢到耳后。她耳根红了。"),

    # --- 场景三：便利店（10月3日深夜）---
    ("m08", "10月3日", "深夜", "便利店",
     "夜里去觅食，隔着窗玻璃望见她与新人并肩而行。我把吃了一半的关东煮扔了。"),

    # --- 场景四：加班（10月5日）---
    ("m09", "10月5日", "晚上", "工位区",
     "深夜巡视一圈，她趴在桌上睡熟了。我解下外套覆在她身上。"),
    ("m10", "10月5日", "凌晨", "工位",
     "凌晨两点发了条消息：'在忙什么'。他秒回'改方案'。后来我又跟了一句'其实我不困'。"),
    ("m11", "10月5日", "凌晨", "工位",
     "凌晨两点收到她的消息：'在忙什么'。我说在赶工，她回'别熬了'。她又追了一句'那我也不睡'。"),

    # --- 场景五：电梯口（10月5日凌晨）---
    ("m12", "10月5日", "凌晨", "电梯口",
     "深夜候梯，三人相顾无言。门开时我让他们先行，自己转向了楼梯间。"),
    ("m13", "10月5日", "凌晨", "楼梯间",
     "她说了句'我走楼梯'便消失在防火门后。"),

    # --- 场景六：咖啡店（10月8日）---
    ("m14", "10月8日", "早上", "咖啡店",
     "周一晨间去楼下，她已端坐店内。看见我便将一杯推过来，说'多买了一杯'。"),
    ("m15", "10月8日", "早上", "咖啡店",
     "提早二十分钟到店点了两杯。见他进来立刻摆出'顺手多买'的姿态。实则是掐着点儿来的。"),
    ("m16", "10月8日", "早上", "咖啡店",
     "到店时新人与顾明汐已对坐无言。我立在门口看了一会儿才进去，眉心微蹙。"),
    ("m17", "10月8日", "周末", "咖啡店",
     "周末也去了同一家店，她不在。店员说今天没看到那个总买两杯的人。"),  # trap: 同地点+同动作

    # --- 场景七：楼梯间眼泪（10月10日）---
    ("m18", "10月10日", "下午", "楼梯间",
     "评审会上被当众点出'交互逻辑说不过去'。散场后躲进楼梯间埋首哭了十分钟。"),
    ("m19", "10月10日", "下午", "楼梯间",
     "会后不见她人影，寻至楼梯间。她蜷在台阶上抽噎。我挨着她坐下，把纸巾递过去。"),
    ("m20", "10月10日", "下午", "楼梯间",
     "他没追问，只是把纸巾塞到我手里。后来他开口：'你的方案我看过，真的挺好'。"),
    ("m21", "10月10日", "傍晚", "楼梯间窗边",
     "从楼梯间的窗望下去，新人正送她回工位。两人隔着一步距离，未交一语。"),

    # --- 额外记忆（增加噪声）---
    ("m22", "10月6日", "晚上", "会议室",
     "又是加班到很晚，会议室里只剩我们两个人。她帮我改方案，我帮她改图纸。"),
    ("m23", "10月9日", "凌晨", "工位",
     "又是凌晨两点，这次谁也没发消息。对话框安安静静，像两个赌气的小孩。"),
    ("m24", "10月11日", "下午", "楼梯间",
     "又去楼梯间找她，这次她没哭。看见我就笑了，说'你怎么知道我在这里'。"),
]


# ---------------------------------------------------------------------------
# 24 条 query
# ---------------------------------------------------------------------------

QUERIES = [
    # ---- exact ----
    ("exact", "谁帮我按了咖啡机？", ["m01"], ["m02"]),
    ("exact", "评审散场后谁最后一个走？", ["m06"], ["m05"]),
    ("exact", "加班那夜谁把外套盖在她身上？", ["m09"], []),
    ("exact", "谁每天早起去楼下买两杯？", ["m15"], ["m14", "m17"]),
    ("exact", "评审会后谁躲起来哭？", ["m18"], ["m19"]),
    ("exact", "楼梯间里谁先递的纸巾？", ["m19", "m20"], ["m21"]),

    # ---- spoken ----
    ("spoken", "她是不是对我有意思？为啥总在我加班时出现", ["m08"], ["m09"]),
    ("spoken", "那天只剩我俩，我帮她弄头发是啥意思", ["m07"], ["m05"]),
    ("spoken", "凌晨两点问我忙啥，她想聊啥", ["m10", "m11"], ["m23"]),
    ("spoken", "嘴上说是多买的，其实是故意的吧", ["m14", "m15"], ["m17"]),
    ("spoken", "哭的时候他一句话没说，只递了张纸，算哪门子安慰", ["m19", "m20"], ["m18"]),
    ("spoken", "她为啥不坐电梯偏走楼梯", ["m12", "m13"], []),

    # ---- cross-event ----
    ("cross-event", "初见时递咖啡和后来在楼下买两杯，她是不是同一种心思", ["m01", "m14", "m15"], ["m02"]),
    ("cross-event", "评审后她找借口留到最后，跟凌晨发消息说不睡，是不是都在找理由陪我", ["m05", "m10", "m11"], ["m07"]),
    ("cross-event", "给她披外套和走楼梯不坐电梯，她是不是不善表达", ["m09", "m12", "m13"], []),
    ("cross-event", "楼下多买一杯和电梯口让进去，她是不是都在用借口藏着什么", ["m15", "m12"], ["m14"]),
    ("cross-event", "看到我们并肩走在街上她扔了吃的，和后来在楼下看到我们坐着她皱眉，她心情变了吗", ["m08", "m16"], ["m18"]),
    ("cross-event", "被否定后他递纸巾，和那天帮我弄头发，他对我是不是同一种温柔", ["m07", "m19", "m20"], ["m05"]),

    # ---- emotional ----
    ("emotional", "她说趁热喝别烫着，耳尖却红了，啥意思", ["m04"], []),
    ("emotional", "凌晨说其实我不困，她想说啥", ["m10", "m11"], ["m23"]),
    ("emotional", "看见她趴在桌上睡着，给她披衣服时是啥心情", ["m09"], []),
    ("emotional", "说走楼梯，是不是不想打搅我们", ["m12", "m13"], []),
    ("emotional", "在楼梯间哭他没吭声，为啥反倒觉得被懂了", ["m19", "m20"], ["m24"]),
    ("emotional", "多买了一杯还特意早起，这种被悄悄惦记是啥感觉", ["m14", "m15"], ["m17"]),
]


def _get_jieba():
    import jieba, logging
    try: jieba.setLogLevel(logging.ERROR)
    except: pass
    return jieba


def tokenize_fts(text):
    if not text.strip(): return ""
    jieba = _get_jieba()
    return " ".join(t.strip().lower() for t in jieba.cut(text, cut_all=False) if t.strip())


def build_fts_query(text, max_terms=32):
    if not text.strip(): return ""
    jieba = _get_jieba()
    terms, seen = [], set()
    for raw in jieba.cut(text.strip(), cut_all=False):
        t = raw.strip().lower()
        if t and t not in seen:
            seen.add(t)
            terms.append(t)
            if len(terms) >= max_terms: break
    return " OR ".join(f'"{t}"' for t in terms) if terms else ""


def cosine_sim(a, b):
    dot = sum(x*y for x,y in zip(a,b))
    na, nb = math.sqrt(sum(x*x for x in a)), math.sqrt(sum(y*y for y in b))
    return dot/(na*nb) if na and nb else 0.0


def vec_to_blob(vec): return array.array("f", vec).tobytes()
def blob_to_vec(blob): return array.array("f", blob).tolist()


def init_db(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY, memory_owner TEXT NOT NULL, game_date TEXT NOT NULL,
        title TEXT DEFAULT '', time TEXT DEFAULT '', location TEXT DEFAULT '',
        content TEXT NOT NULL, importance INTEGER DEFAULT 3, embedding BLOB NOT NULL)""")
    conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
        content, tokenize='unicode61')""")
    conn.commit()


def insert_memories(conn, memories):
    for m in memories:
        conn.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?)",
            (m["id"], m["memory_owner"], m["game_date"], m["title"], m["time"],
             m["location"], m["content"], m["importance"], m["embedding"]))
        rowid = conn.execute("SELECT rowid FROM memories WHERE id=?", (m["id"],)).fetchone()[0]
        conn.execute("INSERT INTO memories_fts(rowid, content) VALUES (?, ?)",
            (rowid, tokenize_fts(m["content"])))
    conn.commit()


def fetch_all_memories(conn):
    rows = conn.execute("SELECT id, content, embedding FROM memories WHERE memory_owner=?", (GROUP,)).fetchall()
    return [{"id": r[0], "content": r[1], "embedding": blob_to_vec(r[2])} for r in rows]


def search_vector(all_mems, qvec, limit):
    scored = sorted(((cosine_sim(qvec, m["embedding"]), m["id"]) for m in all_mems), reverse=True)
    return [mid for _, mid in scored[:limit]]


def search_bm25(conn, query, limit):
    fts_q = build_fts_query(query)
    if not fts_q: return []
    try:
        rows = conn.execute("""
            SELECT m.id, bm25(memories_fts) AS rank FROM memories_fts f
            JOIN memories m ON m.rowid = f.rowid
            WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?""", (fts_q, limit)).fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        print(f"  [warn] BM25 error: {e}")
        return []


def rrf_fusion(vec_ids, bm25_ids, k=60):
    """Reciprocal Rank Fusion"""
    scores = {}
    for rank, mid in enumerate(vec_ids):
        scores[mid] = scores.get(mid, 0) + 1.0 / (k + rank + 1)
    for rank, mid in enumerate(bm25_ids):
        scores[mid] = scores.get(mid, 0) + 1.0 / (k + rank + 1)
    return sorted(scores.keys(), key=lambda x: scores[x], reverse=True)


def search_hybrid(all_mems, conn, qvec, bm25q, limit):
    vec_ids = search_vector(all_mems, qvec, 15)
    bm25_ids = search_bm25(conn, bm25q, 15)
    fused = rrf_fusion(vec_ids, bm25_ids)
    return fused[:limit]


def search_hybrid_rerank(all_mems, conn, query, qvec, bm25q, limit):
    cands = search_hybrid(all_mems, conn, qvec, bm25q, 10)
    if not RERANK_MODEL or not cands: return cands
    id2c = {m["id"]: m["content"] for m in all_mems}
    docs = [id2c.get(mid, "") for mid in cands]
    scores = rerank_texts_sync(query, docs)
    if scores is None: return cands
    return [mid for _, mid in sorted(zip(scores, cands), reverse=True)[:limit]]


def hit_at(ids, gt, k): return any(x in gt for x in ids[:k])
def reciprocal(ids, gt):
    for i, x in enumerate(ids, 1):
        if x in gt: return 1.0 / i
    return 0.0


def main():
    print("=== 实验 A v2 ===\n")
    mem_texts = [m[4] for m in MEMORIES]
    print(f"Embedding {len(mem_texts)} memories...")
    t0 = time.time()
    mem_embs = embed_texts_sync(mem_texts)
    print(f"  {time.time()-t0:.1f}s, dim={len(mem_embs[0])}")

    conn = sqlite3.connect(_EXP_DB)
    init_db(conn)
    records = []
    for (mid, date, tv, loc, content), emb in zip(MEMORIES, mem_embs):
        records.append({"id": mid, "memory_owner": GROUP, "game_date": date,
            "title": f"{date} {loc}", "time": tv, "location": loc,
            "content": content, "importance": 3, "embedding": vec_to_blob(emb)})
    insert_memories(conn, records)
    all_mems = fetch_all_memories(conn)

    q_texts = [q for _, q, _, _ in QUERIES]
    print(f"Embedding {len(q_texts)} queries...")
    t0 = time.time()
    q_embs = embed_texts_sync(q_texts)
    print(f"  {time.time()-t0:.1f}s")

    names = ["vector", "bm25", "hybrid_rrf", "hybrid+rerank"]
    per_type = {nm: {"exact": [], "spoken": [], "cross-event": [], "emotional": []} for nm in names}
    overall = {nm: {"r1": [], "r3": [], "r5": [], "mrr": [], "r1_rel": []} for nm in names}

    print("\nRunning pipelines...")
    for (qtype, q, p0, p1), qv in zip(QUERIES, q_embs):
        plans = [
            ("vector", search_vector(all_mems, qv, 5)),
            ("bm25", search_bm25(conn, q, 5)),
            ("hybrid_rrf", search_hybrid(all_mems, conn, qv, q, 5)),
            ("hybrid+rerank", search_hybrid_rerank(all_mems, conn, q, qv, q, 5)),
        ]
        for name, ids in plans:
            overall[name]["r1"].append(hit_at(ids, p0, 1))
            overall[name]["r3"].append(hit_at(ids, p0, 3))
            overall[name]["r5"].append(hit_at(ids, p0, 5))
            overall[name]["mrr"].append(reciprocal(ids, p0))
            overall[name]["r1_rel"].append(hit_at(ids, p0 + p1, 1))
            per_type[name][qtype].append(hit_at(ids, p0, 1))
        time.sleep(0.3)

    print("\n=== Results (5 scenarios x 24 memories x 24 queries) ===")
    print(f"{'pipeline':<14} {'R@1':>6} {'R@3':>6} {'R@5':>6} {'MRR':>7}   exact/spoken/cross/emotional")
    rows = []
    for name in names:
        r = overall[name]
        nq = len(r["r1"])
        r1, r3, r5 = sum(r["r1"])/nq, sum(r["r3"])/nq, sum(r["r5"])/nq
        mrr = sum(r["mrr"])/nq
        e, s, c, em = [sum(per_type[name][t])/6 for t in ["exact", "spoken", "cross-event", "emotional"]]
        print(f"{name:<14} {r1:6.3f} {r3:6.3f} {r5:6.3f} {mrr:7.3f}   {e:.2f}/{s:.2f}/{c:.2f}/{em:.2f}")
        rows.append({"pipeline": name, "r@1": r1, "r@3": r3, "r@5": r5, "mrr": mrr,
                     "r1_exact": e, "r1_spoken": s, "r1_cross": c, "r1_emotional": em})

    out = os.path.join(RESULTS_DIR, "exp1_retrieval_v2.json")
    with open(out, "w") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"\nSaved -> {out}")

    print("\n--- P0+P1 relaxed R@1 ---")
    for name in names:
        r = overall[name]
        print(f"  {name:<14} {sum(r['r1_rel'])/len(r['r1_rel']):.3f}")
    conn.close()


if __name__ == "__main__":
    main()
