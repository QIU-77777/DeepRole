"""实验 C：纯向量检索在长期记忆与人格沉淀上的召回效果验证（v3）。

改进：query 改为具体事实性查询（查询具体事件/行为/事实），
而非抽象总结（查询性格/关系/模式）。

用法：
    /usr/local/bin/python3 bench/exp3_vector_only.py
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

_EXP_DB = os.path.join(RESULTS_DIR, "exp3_vectors.sqlite")
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

def embed_texts_sync(texts):
    return asyncio.run(embed_texts(texts))

def cosine_sim(a, b):
    dot = sum(x*y for x,y in zip(a,b))
    na, nb = math.sqrt(sum(x*x for x in a)), math.sqrt(sum(y*y for y in b))
    return dot/(na*nb) if na and nb else 0.0

def vec_to_blob(vec): return array.array("f", vec).tobytes()
def blob_to_vec(blob): return array.array("f", blob).tolist()


# ---------------------------------------------------------------------------
# EpisodeMemory：12 条事件记忆
# ---------------------------------------------------------------------------

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


# EpisodeMemory queries: 具体事实性查询
EPISODE_QUERIES = [
    ("event", "谁帮我按了咖啡机", ["e01"], []),
    ("event", "评审散场后谁借口收拾图纸留下了", ["e03"], []),
    ("event", "加班那夜谁把外套盖在她身上", ["e06"], []),
    ("event", "周一早上谁说多买了一杯", ["e09"], []),
    ("event", "谁在楼梯间递了纸巾", ["e11"], ["e10"]),
    ("emotion", "她看到我和别人一起为啥不高兴", ["e05"], []),
    ("emotion", "她帮我弄头发时为啥耳根红了", ["e04"], []),
    ("emotion", "她为啥总找借口多待一会儿", ["e03", "e07"], ["e09"]),
    ("emotion", "她走楼梯是不是在躲什么", ["e08"], []),
    ("infer", "她表达感情的方式是怎样的", ["e02", "e04", "e06"], ["e03"]),
    ("infer", "她面对我和别人亲近时会怎样", ["e05", "e08"], []),
    ("infer", "她为啥说趁热喝别烫着然后回头", ["e02"], []),
]


# ---------------------------------------------------------------------------
# Understanding：12 条人格沉淀
# ---------------------------------------------------------------------------

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


# Understanding queries: 改为更具体的查询
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


def hit_at(ids, gt, k): return any(x in gt for x in ids[:k])
def reciprocal(ids, gt):
    for i, x in enumerate(ids, 1):
        if x in gt: return 1.0 / i
    return 0.0


def evaluate_group(group_name, mems, queries, q_embs, label):
    per_type = {}
    overall = {"r1": [], "r3": [], "r5": [], "mrr": [], "r1_rel": []}
    
    for (qtype, q, p0, p1), qv in zip(queries, q_embs):
        ids = search_vector(mems, qv, 5)
        overall["r1"].append(hit_at(ids, p0, 1))
        overall["r3"].append(hit_at(ids, p0, 3))
        overall["r5"].append(hit_at(ids, p0, 5))
        overall["mrr"].append(reciprocal(ids, p0))
        overall["r1_rel"].append(hit_at(ids, p0 + p1, 1))
        per_type.setdefault(qtype, []).append(hit_at(ids, p0, 1))
    
    nq = len(overall["r1"])
    r1 = sum(overall["r1"]) / nq
    r3 = sum(overall["r3"]) / nq
    r5 = sum(overall["r5"]) / nq
    mrr = sum(overall["mrr"]) / nq
    
    print(f"\n--- {label} ---")
    print(f"  Overall R@1={r1:.3f}  R@3={r3:.3f}  R@5={r5:.3f}  MRR={mrr:.3f}")
    print(f"  Per-type:")
    for qt, vals in per_type.items():
        print(f"    {qt}: {sum(vals)/len(vals):.3f}")
    
    return {"r1": r1, "r3": r3, "r5": r5, "mrr": mrr, "per_type": {k: sum(v)/len(v) for k,v in per_type.items()}}


def main():
    print("=== 实验 C v3：纯向量检索验证 ===\n")
    
    ep_texts = [e[2] for e in EPISODES]
    ut_texts = [u[2] for u in UNDERSTANDINGS]
    print(f"Embedding {len(ep_texts)} episodes + {len(ut_texts)} understandings...")
    t0 = time.time()
    ep_embs = embed_texts_sync(ep_texts)
    ut_embs = embed_texts_sync(ut_texts)
    print(f"  {time.time()-t0:.1f}s")
    
    conn = sqlite3.connect(_EXP_DB)
    init_db(conn)
    insert_episodes(conn, EPISODES, ep_embs)
    insert_understandings(conn, UNDERSTANDINGS, ut_embs)
    
    ep_mems = fetch_group(conn, GROUP_E)
    ut_mems = fetch_group(conn, GROUP_U)
    
    epq_texts = [q for _, q, _, _ in EPISODE_QUERIES]
    utq_texts = [q for _, q, _, _ in UNDERSTANDING_QUERIES]
    print(f"Embedding {len(epq_texts)} episode queries + {len(utq_texts)} understanding queries...")
    t0 = time.time()
    epq_embs = embed_texts_sync(epq_texts)
    utq_embs = embed_texts_sync(utq_texts)
    print(f"  {time.time()-t0:.1f}s")
    
    ep_result = evaluate_group(GROUP_E, ep_mems, EPISODE_QUERIES, epq_embs, "EpisodeMemory (事件记忆)")
    ut_result = evaluate_group(GROUP_U, ut_mems, UNDERSTANDING_QUERIES, utq_embs, "Understanding (人格沉淀)")
    
    all_r1 = ep_result["r1"] + ut_result["r1"]
    print(f"\n=== 总结 ===")
    print(f"  EpisodeMemory R@1:  {ep_result['r1']:.3f}")
    print(f"  Understanding R@1:  {ut_result['r1']:.3f}")
    print(f"  平均 R@1:           {all_r1/2:.3f}")
    
    results = {
        "episode_memory": ep_result,
        "understanding": ut_result,
        "avg_r1": all_r1 / 2,
    }
    out = os.path.join(RESULTS_DIR, "exp3_vector_only.json")
    with open(out, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nSaved -> {out}")
    conn.close()


if __name__ == "__main__":
    main()
