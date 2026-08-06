"""实验 B：Token 占用曲线（无记忆全量注入 vs DeepRole 混合召回窗口）。

合成 60 轮《Partial·偏心》对话，计算两套系统每轮的 LLM 输入规模：
- 基线 A（无记忆系统）：把截至当前轮的全部历史原文拼进 context。
- DeepRole：检索 Top3 EpisodeMemory + 最近 8 个 turn 的历史窗口（按项目 config）。

每轮 context 规模 = 历史片段字符数 + 记忆块字符数 + 固定 prompt 骨架。

token 估算：中文按 1 字符 ≈ 1 token、英文按 4 字符 ≈ 1 token 的保守近似，
在脚本里显式计算并标注方法论。

纯离线可复现（不调用任何外部 API）。

用法：
    python bench/exp2_tokens.py
  输出：bench/results/exp2_tokens.csv
"""
import os
import csv
import random

random.seed(42)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(PROJECT_ROOT, "bench", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

# ---- 项目真实配置（取自 config.toml）----
HISTORY_LOW = 8          # 多轮高低水位截断 low
EPISODE_SEARCH_LIMIT = 3  # 检索返回条数
WINDOW_TURNS = HISTORY_LOW

# ---- 合成 60 轮对话，交替旁白/角色，贴近《偏心》场景 ----
AGENTS = ["陆时川（产品经理）", "江知夏（UI 设计师）", "顾明汐（研发总监）"]


def make_turn(i):
    """第 i 轮返回 (role, text)。text 长度模拟真实对话（40~140 字）。"""
    if i % 5 == 0:
        role = "旁白"
        text = f"第{i//5+1}天，办公室里的气氛有些微妙，项目进入关键时刻。"
    else:
        role = AGENTS[(i * 2) % 3]
        text = (
            f"关于这个需求，我希望能再对齐一下范围，把优先级排清楚，"
            f"避免后面返工。我们上周其实就讨论过类似的方案，当时卡在交付时间上，"
            f"今天重点是想把接口边界和验收标准一次性定下来，减少来回沟通的成本。"
            f"（第{i}轮补充信息）"
        )
    return role, text


turns = [make_turn(i) for i in range(1, 61)]


def history_up_to(k):
    """前 k 个 turn 的原文拼接。"""
    return "\n".join(f"{r}: {t}" for r, t in turns[:k])


def deeprole_window_up_to(k):
    """最近 HISTORY_LOW 个 turn。"""
    start = max(0, k - WINDOW_TURNS)
    return "\n".join(f"{r}: {t}" for r, t in turns[start:k])


def recalled_memories(k):
    """模拟检索返回 Top3 记忆。随轮数增加记忆更丰富（更贴近真实）。"""
    mems = []
    n_episodes = min(3, 1 + k // 20)  # 前 20 轮 1 条，之后逐渐到 3 条
    for j in range(n_episodes):
        mems.append(
            f"## {1+j}月{1+j}日\n"
            f"- **标题**：迭代评审与方向对齐\n"
            f"- **时间**：上午\n- **地点**：会议室\n- **在场**：陆时川、江知夏\n"
            f"- **内容**：产品、设计、研发三方对齐了本轮迭代的优先级，"
            f"确认先解决性能问题，再推进视觉改版，争议点在交付节奏。"
        )
    return "\n\n---\n\n".join(mems)


SKELETON = (
    "<status>\n产品团队当前状态：推进中。\n"
    "**和玩家的关系**：协作\n</status>\n"
    "玩家新消息：\n"
)


def est_tokens(text: str) -> int:
    """保守 token 近似：中文 1 字符/个，英文 4 字符/个，标点按中文算。"""
    cn = 0
    en = 0
    for ch in text:
        if ord(ch) > 0x2E7F:  # CJK 范围
            cn += 1
        elif ch.isalnum():
            en += 1
    return cn + en // 4 + 8  # +8 粗略计入 prompt 结构开销


def run():
    rows = []
    for k in range(1, 61):
        baseline_text = f"最近对话历史:\n\n{history_up_to(k)}"
        dr_history = f"最近对话历史:\n\n{deeprole_window_up_to(k)}"
        dr_mem = f"<relevant_memories>\n{recalled_memories(k)}\n</relevant_memories>"
        dr_text = dr_history + "\n\n---\n\n" + dr_mem

        b_tokens = est_tokens(SKELETON + baseline_text)
        d_tokens = est_tokens(SKELETON + dr_text)
        rows.append({
            "turn": k,
            "baseline_tokens": b_tokens,
            "deeprole_tokens": d_tokens,
            "reduction_pct": round((b_tokens - d_tokens) / b_tokens * 100, 1),
            "baseline_chars": len(baseline_text),
            "deeprole_chars": len(dr_text),
        })

    print("=== 实验 B：Token 占用曲线（60 轮合成对话）===")
    print("turn | baseline(tok) | deeprole(tok) | 下降比例")
    print("-----+---------------+---------------+----------")
    for row in rows:
        if row["turn"] in (5, 10, 20, 30, 40, 50, 60):
            print(f"{row['turn']:>4} | {row['baseline_tokens']:>13} | "
                  f"{row['deeprole_tokens']:>13} | {row['reduction_pct']:>6.1f}%")

    t20 = rows[19]
    t40 = rows[39]
    t60 = rows[59]
    print(f"\n[结论] 第 20 轮下降 {t20['reduction_pct']:.1f}%，"
          f"第 40 轮下降 {t40['reduction_pct']:.1f}%，"
          f"第 60 轮下降 {t60['reduction_pct']:.1f}%")

    out = os.path.join(RESULTS_DIR, "exp2_tokens.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"saved -> {out}")


if __name__ == "__main__":
    run()