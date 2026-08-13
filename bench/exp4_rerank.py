"""
experiment D: Hybrid retrieval + Rerank for abstract queries.

Pipeline: Vector + BM25 -> RRF recall top-10 -> Rerank -> output top-3

Compare:
- Pure Vector (baseline)
- Hybrid RRF (vector + BM25)
- Hybrid + Rerank (vector + BM25 + Rerank)

Focus: Rerank improvement on abstract queries (emotion/infer/trait/belief).

Usage:
    /usr/local/bin/python3 bench/exp4_rerank.py
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

_EXP_DB = os.path.join(RESULTS_DIR, "exp4_vectors.sqlite")
for suffix in ("", "-wal", "-shm"):
    try: os.remove(_EXP_DB + suffix)
    except OSError: pass

GROUP_E = "bench_episodes"
GROUP_U = "bench_understandings"


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


def cosine_sim(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def vec_to_blob(vec): return array.array("f", vec).tobytes()
def blob_to_vec(blob): return array.array("f", blob).tolist()

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

# EpisodeMemory
EPISODES = [
    ("e01", "10月2日", "帮她按了咖啡机按钮，她递纸杯时说了句勉强算你前辈。"),
    ("e02", "10月2日", "故意让杯沿碰了碰他的，问他紧不紧张。自己反倒心跳得厉害。"),
    ("e03", "10月3日", "散场后众人皆去，我借口帮他收拾图纸多待了一会儿。满室只余我俩。"),
    ("e04", "10月3日", "众人散尽，她凑过来帮忙收拾。发丝垂落，我伸手替她拢到耳后。她耳根红了。"),
    ("e05", "10月3日", "夜里去觅食，隔着窗玻璃望见她与新人并肩而行。我把吃了一半的关东煮扔了。"),
    ("e06", "10月5日", "深夜巡视一圈，她趴在桌上睡熟了。我解下外套覆在她身上。"),
    ("e07", "10月5日", "凌晨两点发了条消息：在忙什么。他秒回改方案。后来我又跟了一句其实我不困。"),
    ("e08", "10月5日", "深夜候梯，三人相顾无言。门开时我让他们先行，自己转向了楼梯间。"),
    ("e09", "10月8日", "周一晨间去楼下，她已端坐店内。看见我便将一杯推过来，说多买了一杯。"),
    ("e10", "10月10日", "评审会上被当众点出交互逻辑说不过去。散场后躲进楼梯间埋首哭了十分钟。"),
    ("e11", "10月10日", "会后不见她人影，寻至楼梯间。她蜷在台阶上抽噎。我挨着她坐下，把纸巾递过去。"),
    ("e12", "10月11日", "又去楼梯间找她，这次她没哭。看见我就笑了，说你怎么知道我在这里。"),
]

EPISODE_QUERIES = [
    ("fact", "谁帮我按了咖啡机", ["e01"], []),
    ("fact", "评审散场后谁借口收拾图纸留下了", ["e03"], []),
    ("fact", "加班那夜谁把外套盖在她身上", ["e06"], []),
    ("fact", "周一早上谁说多买了一杯", ["e09"], []),
    ("emotion", "她是不是对我有意思？为啥总在我加班时出现", ["e05"], ["e06"]),
    ("emotion", "只剩我俩时我帮她弄头发是啥意思", ["e04"], ["e03"]),
    ("emotion", "她为啥总找借口多待一会儿", ["e03", "e07"], ["e09"]),
    ("emotion", "她走楼梯是不是在躲什么", ["e08"], []),
    ("infer", "她不善表达，是不是总用行动代替说话", ["e06", "e08"], ["e04"]),
    ("infer", "她面对我和别人亲近时会怎样", ["e05", "e08"], []),
    ("infer", "她为啥说趁热喝别烫着然后回头", ["e02"], []),
    ("infer", "她对我到底是啥感觉", ["e01", "e02", "e05"], []),
]

# Understanding
UNDERSTANDINGS = [
    ("u01", "行动代替言语", "她习惯用行动表达关心，而非言语。早起买咖啡、加班披外套、评审后找借口留下。"),
    ("u02", "外表内敛内心细腻", "她外表沉稳内敛，实则心思细腻。越是在意的人，越不敢直说。"),
    ("u03", "害怕被看穿", "她害怕被看穿。一旦有人靠近，会下意识回避。不是不喜欢，是怕自己不够好。"),
    ("u04", "偏爱藏在细节", "偏爱藏在细节里：多问一句、多留一步、多买一杯。看似随意，实则是掐着点儿来的。"),
    ("u05", "公事到暧昧", "从协作到独处，从公事到暧昧。她没有说破，但我能感觉到她在一点点靠近。"),
    ("u06", "工作能力强", "研发总监，能力强、靠谱、有担当。开会能把产品撕成两半，私下会绕工位区看谁在加班。"),
    ("u07", "公开和私下不一样", "公开场合公事公办，说话清楚简洁。但耳尖会红，会假装多买一杯，会在楼梯间偷偷哭。"),
    ("u08", "过去被否定", "她曾经被说过不需要她。所以不轻易表达需要，但会把偏爱藏在安排里。"),
    ("u09", "用沉默掩饰嫉妒", "看到我和别人亲近，不会发火，只会话变少、笑变少。然后用工作话题来掩饰。"),
    ("u10", "不说温柔的话", "不说温柔的话，但会做温柔的事：披外套、递纸巾、凌晨发消息问在忙什么。"),
    ("u11", "想靠近又怕靠近", "既想靠近，又怕靠近。会故意碰杯沿引起注意，又会说趁热喝别烫着然后转身走掉。"),
    ("u12", "记得我的作息", "她记得我的作息，知道我几点出门，会提前二十分钟到咖啡店等我。不说，但都做了。"),
]

UNDERSTANDING_QUERIES = [
    ("belief", "她表达感情的方式是什么", ["u01", "u04"], ["u10"]),
    ("belief", "她对我是个什么态度", ["u05", "u04"], []),
    ("belief", "她看到我和别人一起会怎样", ["u09"], []),
    ("belief", "她为啥不敢直说", ["u03", "u08"], ["u02"]),
    ("fact", "她做过哪些温柔的事", ["u10", "u01"], []),
    ("fact", "她为啥提前二十分钟到咖啡店", ["u12"], []),
    ("fact", "她开会时是什么样", ["u06"], []),
    ("fact", "她私下和公开有什么不同", ["u07"], []),
    ("infer", "她为啥总找借口", ["u01", "u11"], []),
    ("infer", "她为啥看到我就笑", ["u12", "u05"], []),
    ("infer", "她有啥矛盾的行为", ["u11", "u03"], []),
    ("infer", "她怎么掩饰嫉妒", ["u09"], []),
]

def init_db(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY, memory_owner TEXT NOT NULL, game_date TEXT NOT NULL,
        title TEXT DEFAULT '', time TEXT DEFAULT '', location TEXT DEFAULT '',
        content TEXT NOT NULL, importance INTEGER DEFAULT 3, embedding BLOB NOT NULL)""")
    conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
        content, tokenize='unicode61')""")
    conn.commit()

def insert_episodes(conn, episodes, embeddings):
    for (mid, date, content), emb in zip(episodes, embeddings):
        conn.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?)",
            (mid, GROUP_E, date, f"{date}", "", "", content, 3, vec_to_blob(emb)))
        rowid = conn.execute("SELECT rowid FROM memories WHERE id=?", (mid,)).fetchone()[0]
        conn.execute("INSERT INTO memories_fts(rowid, content) VALUES (?, ?)", (rowid, content))
    conn.commit()

def insert_understandings(conn, understandings, embeddings):
    for (mid, subject, content), emb in zip(understandings, embeddings):
        conn.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?)",
            (mid, GROUP_U, "", subject, "", "", content, 3, vec_to_blob(emb)))
        rowid = conn.execute("SELECT rowid FROM memories WHERE id=?", (mid,)).fetchone()[0]
        conn.execute("INSERT INTO memories_fts(rowid, content) VALUES (?, ?)", (rowid, content))
    conn.commit()

def fetch_group(conn, group):
    rows = conn.execute("SELECT id, content, embedding FROM memories WHERE memory_owner=?", (group,)).fetchall()
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
    scores = {}
    for rank, mid in enumerate(vec_ids):
        scores[mid] = scores.get(mid, 0) + 1.0 / (k + rank + 1)
    for rank, mid in enumerate(bm25_ids):
        scores[mid] = scores.get(mid, 0) + 1.0 / (k + rank + 1)
    return sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

def search_hybrid_rerank(all_mems, conn, query, qvec, limit, vec_w=0.75):
    """Vector + BM25 -> RRF -> Rerank"""
    # Step 1: Vector recall top-15
    vec_ids = search_vector(all_mems, qvec, 15)
    # Step 2: BM25 recall top-15
    bm25_ids = search_bm25(conn, query, 15)
    # Step 3: RRF fusion -> top-10
    cands = rrf_fusion(vec_ids, bm25_ids)[:10]
    if not RERANK_MODEL or not cands:
        return cands[:limit]
    # Step 4: Rerank
    id2c = {m["id"]: m["content"] for m in all_mems}
    docs = [id2c.get(mid, "") for mid in cands]
    scores = rerank_texts_sync(query, docs)
    if scores is None:
        return cands[:limit]
    reranked = sorted(zip(scores, cands), key=lambda x: x[0], reverse=True)
    return [mid for _, mid in reranked[:limit]]

def hit_at(ids, gt, k): return any(x in gt for x in ids[:k])
def reciprocal(ids, gt):
    for i, x in enumerate(ids, 1):
        if x in gt: return 1.0 / i
    return 0.0

def evaluate_pipeline(name, mems, queries, q_embs, conn, limit=3):
    per_type = {}
    overall = {"r1": [], "r3": [], "r5": [], "mrr": [], "r1_rel": []}
    
    for (qtype, q, p0, p1), qv in zip(queries, q_embs):
        ids = search_hybrid_rerank(mems, conn, q, qv, limit)
        overall["r1"].append(hit_at(ids, p0, 1))
        overall["r3"].append(hit_at(ids, p0, 3))
        overall["mrr"].append(reciprocal(ids, p0))
        overall["r1_rel"].append(hit_at(ids, p0 + p1, 1))
        per_type.setdefault(qtype, []).append(hit_at(ids, p0, 1))
    
    nq = len(overall["r1"])
    r1 = sum(overall["r1"]) / nq
    mrr = sum(overall["mrr"]) / nq
    
    print(f"\n--- {name} ---")
    print(f"  Overall R@1={r1:.3f}  MRR={mrr:.3f}")
    print(f"  Per-type:")
    for qt, vals in per_type.items():
        print(f"    {qt}: {sum(vals)/len(vals):.3f}")
    
    return {"r1": r1, "mrr": mrr, "per_type": {k: sum(v)/len(v) for k,v in per_type.items()}}

def evaluate_vector(name, mems, queries, q_embs, limit=3):
    """Pure vector baseline"""
    per_type = {}
    overall = {"r1": [], "r3": [], "r5": [], "mrr": [], "r1_rel": []}
    
    for (qtype, q, p0, p1), qv in zip(queries, q_embs):
        ids = search_vector(mems, qv, limit)
        overall["r1"].append(hit_at(ids, p0, 1))
        overall["r3"].append(hit_at(ids, p0, 3))
        overall["mrr"].append(reciprocal(ids, p0))
        overall["r1_rel"].append(hit_at(ids, p0 + p1, 1))
        per_type.setdefault(qtype, []).append(hit_at(ids, p0, 1))
    
    nq = len(overall["r1"])
    r1 = sum(overall["r1"]) / nq
    mrr = sum(overall["mrr"]) / nq
    
    print(f"\n--- {name} ---")
    print(f"  Overall R@1={r1:.3f}  MRR={mrr:.3f}")
    print(f"  Per-type:")
    for qt, vals in per_type.items():
        print(f"    {qt}: {sum(vals)/len(vals):.3f}")
    
    return {"r1": r1, "mrr": mrr, "per_type": {k: sum(v)/len(v) for k,v in per_type.items()}}

def main():
    print("=== Experiment D: Hybrid + Rerank ===\n")
    
    # Embedding
    ep_texts = [e[2] for e in EPISODES]
    ut_texts = [u[2] for u in UNDERSTANDINGS]
    print(f"Embedding {len(ep_texts)} episodes + {len(ut_texts)} understandings...")
    t0 = time.time()
    ep_embs = embed_texts_sync(ep_texts)
    ut_embs = embed_texts_sync(ut_texts)
    print(f"  {time.time()-t0:.1f}s")
    
    # DB
    conn = sqlite3.connect(_EXP_DB)
    init_db(conn)
    insert_episodes(conn, EPISODES, ep_embs)
    insert_understandings(conn, UNDERSTANDINGS, ut_embs)
    
    ep_mems = fetch_group(conn, GROUP_E)
    ut_mems = fetch_group(conn, GROUP_U)
    
    # Query embeddings
    epq_texts = [q for _, q, _, _ in EPISODE_QUERIES]
    utq_texts = [q for _, q, _, _ in UNDERSTANDING_QUERIES]
    print(f"Embedding {len(epq_texts)} episode queries + {len(utq_texts)} understanding queries...")
    t0 = time.time()
    epq_embs = embed_texts_sync(epq_texts)
    utq_embs = embed_texts_sync(utq_texts)
    print(f"  {time.time()-t0:.1f}s")
    
    # Evaluate
    print("\n" + "="*60)
    print("RESULTS")
    print("="*60)
    
    ep_vec = evaluate_vector("EpisodeMemory - Pure Vector", ep_mems, EPISODE_QUERIES, epq_embs)
    ut_vec = evaluate_vector("Understanding - Pure Vector", ut_mems, UNDERSTANDING_QUERIES, utq_embs)
    
    ep_hr = evaluate_pipeline("EpisodeMemory - Hybrid+Rerank", ep_mems, EPISODE_QUERIES, epq_embs, conn)
    ut_hr = evaluate_pipeline("Understanding - Hybrid+Rerank", ut_mems, UNDERSTANDING_QUERIES, utq_embs, conn)
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"{'Group':<20} {'Vector R@1':>12} {'Hybrid+Rerank R@1':>18} {'Improvement':>12}")
    print("-"*62)
    ep_imp = ep_hr['r1'] - ep_vec['r1']
    ut_imp = ut_hr['r1'] - ut_vec['r1']
    print(f"{'EpisodeMemory':<20} {ep_vec['r1']:>12.3f} {ep_hr['r1']:>18.3f} {ep_imp:>+12.3f}")
    print(f"{'Understanding':<20} {ut_vec['r1']:>12.3f} {ut_hr['r1']:>18.3f} {ut_imp:>+12.3f}")
    print(f"{'Average':<20} {(ep_vec['r1']+ut_vec['r1'])/2:>12.3f} {(ep_hr['r1']+ut_hr['r1'])/2:>18.3f} {(ep_imp+ut_imp)/2:>+12.3f}")
    
    # Abstract query improvement
    print("\n--- Abstract Query Improvement ---")
    for qt in ['emotion', 'infer', 'belief']:
        v_ep = ep_vec.get('per_type', {}).get(qt, 0)
        h_ep = ep_hr.get('per_type', {}).get(qt, 0)
        v_ut = ut_vec.get('per_type', {}).get(qt, 0)
        h_ut = ut_hr.get('per_type', {}).get(qt, 0)
        if v_ep > 0 or v_ut > 0:
            print(f"  {qt}: Ep {v_ep:.2f}->{h_ep:.2f} ({h_ep-v_ep:+.2f}) | Ut {v_ut:.2f}->{h_ut:.2f} ({h_ut-v_ut:+.2f})")
    
    # Save
    results = {
        "episode_vector": ep_vec,
        "episode_hybrid_rerank": ep_hr,
        "understanding_vector": ut_vec,
        "understanding_hybrid_rerank": ut_hr,
        "improvement": {
            "episode": ep_imp,
            "understanding": ut_imp,
            "average": (ep_imp + ut_imp) / 2
        }
    }
    out = os.path.join(RESULTS_DIR, "exp4_rerank.json")
    with open(out, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nSaved -> {out}")
    conn.close()

if __name__ == "__main__":
    main()
