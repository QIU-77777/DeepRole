"""实验 C：consolidation 压缩比与耗时（真实 LLM 调用）。

在项目里构造一段 16 轮的剧情弧（旁白 + 玩家 + 江知夏），
走项目真实的 MemoryConsolidationFlow.consolidate_agent：
1. EpisodeMemoryGenerator 生成 EpisodeMemory（closure 判定可绕过，直接指定 until_turn）
2. understanding patch

测量：
- raw 对话字符数 vs EpisodeMemory.content 字符数 → 压缩比
- consolidation 墙钟耗时

⚠️ 隔离保护：脚本会在 data/runtime 上写入测试 raw 消息与 memory_draft，
并生成 EpisodeMemory / Understanding。运行前自动把 data/runtime 备份到
bench/results/runtime_backup_<ts>/，结束后恢复。请勿在重要存档运行中执行。

真实 LLM 调用（DeepSeek-V4-Flash），会消耗少量 token。

用法：
    python bench/exp3_consolidation.py
  输出：控制台 + bench/results/exp3_consolidation.json
"""
import os
import sys
import json
import time
import shutil

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

RESULTS_DIR = os.path.join(PROJECT_ROOT, "bench", "results")
RUNTIME_DIR = os.path.join(PROJECT_ROOT, "data", "runtime")

AGENT = "chenxiao"  # 江知夏


def backup_runtime() -> str:
    """备份 runtime 目录；返回备份路径。"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(RESULTS_DIR, f"runtime_backup_{ts}")
    if os.path.exists(RUNTIME_DIR):
        shutil.copytree(RUNTIME_DIR, dst)
    print(f"[隔离] data/runtime 已备份 -> {dst}")
    return dst


def restore_runtime(bak: str) -> None:
    """从备份恢复 runtime 目录。"""
    shutil.rmtree(RUNTIME_DIR, ignore_errors=True)
    if os.path.exists(bak):
        shutil.copytree(bak, RUNTIME_DIR)
    print("[隔离] data/runtime 已从备份恢复")


def build_arc():
    """16 轮剧情弧：设计稿延期事件完整闭环。"""
    return [
        ("narrator", "1月5日 上午，会议室，陆时川、江知夏、顾明汐在场。", ["chenxiao", "guyining"]),
        ("player", "这版首页设计稿什么时候能交付？", ["chenxiao", "guyining"]),
        ("narrator", "江知夏盯着画板，犹豫了一下。", ["chenxiao"]),
        ("chenxiao", "交互走查又返工了三次，动画效果这边改了很多版，可能要推到周五。", ["chenxiao"]),
        ("player", "周五有点晚，发布会物料都在等。", ["chenxiao", "guyining"]),
        ("narrator", "顾明汐抬头，敲了敲桌子。", ["chenxiao", "guyining"]),
        ("guyining", "性能那边也能帮忙压缩一些动画资源，但我们接口要先冻结。", ["chenxiao", "guyining"]),
        ("player", "那能不能并行？设计先出静态稿。", ["chenxiao", "guyining"]),
        ("chenxiao", "静态稿没问题，但动态效果需要等性能方案确定。", ["chenxiao"]),
        ("narrator", "会议室内气氛缓和了一些。", ["chenxiao", "guyining"]),
        ("player", "那就周四交静态稿，动画后补。", ["chenxiao", "guyining"]),
        ("chenxiao", "好，我今晚加班把静态稿赶出来。", ["chenxiao"]),
        ("narrator", "1月6日 深夜，设计工位。", ["chenxiao"]),
        ("chenxiao", "终于改完了，字体版权那边也重新确认过了。", ["chenxiao"]),
        ("player", "辛苦了，明天评审会直接用。", ["chenxiao", "guyining"]),
        ("narrator", "1月7日 上午，评审会通过，设计稿事件完结。", ["chenxiao", "guyining"]),
    ]


def main():
    import asyncio
    from repository.config import character_path
    from repository.memory_store import append_memory_draft
    from app.consolidation.flow import memory_consolidation_flow

    bak = backup_runtime()

    # 1. 写入 raw 历史（turn 1..16）
    raw = []
    for i, (role, content, visible) in enumerate(build_arc(), start=1):
        msg = {"role": role, "turn": i, "content": content, "visible_to": visible}
        raw.append(msg)

    raw_path = character_path("narrator", "raw", "bench-C.jsonl")
    os.makedirs(os.path.dirname(raw_path), exist_ok=True)
    with open(raw_path, "w", encoding="utf-8") as f:
        for m in raw:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    # 2. 写入 memory_draft（江知夏视角的逐轮记忆草稿，turn 1..16）
    for m in raw:
        if m["role"] == AGENT:
            append_memory_draft(AGENT, m["turn"], m["content"])

    # 3. 直接调用 consolidate_agent（指定 until_turn=16，模拟闭合判定已过）
    t0 = time.monotonic()
    result = asyncio.run(memory_consolidation_flow.consolidate_agent(
        AGENT, until_turn=16, raw_messages=raw
    ))
    dt = time.monotonic() - t0

    print("=== 实验 C：consolidation 压缩比（真实 LLM）===")
    print("skip:", result.skipped, "| reason:", result.skip_reason, "| errors:", result.errors)

    from repository.memory_store import read_memory_jsonl, read_understandings
    episodes = read_memory_jsonl(AGENT)
    total_raw = sum(len(m["content"]) for m in raw)
    print(f"\nraw 剧情弧: {len(raw)} 条消息 / {total_raw} 字符")

    report = {
        "messages": len(raw),
        "raw_chars": total_raw,
        "episodes": [],
        "consolidation_seconds": round(dt, 1),
        "understandings": [],
    }
    if episodes:
        for ep in episodes:
            ratio = total_raw / len(ep.content) if ep.content else 0
            report["episodes"].append({
                "title": ep.title, "date": ep.date,
                "content_chars": len(ep.content),
                "compression_x": round(ratio, 1),
            })
            print(f"\n[EpisodeMemory] {ep.title} | date={ep.date}")
            print(f"  content 字符数: {len(ep.content)} | 压缩比 {ratio:.1f}x")
        avg_chars = sum(e["content_chars"] for e in report["episodes"]) / len(report["episodes"])
        avg_ratio = total_raw / avg_chars if avg_chars else 0
        report["avg_compression_x"] = round(avg_ratio, 1)
        print(f"\n[结论] 平均压缩比 {avg_ratio:.1f}x；consolidation 耗时 {dt:.1f}s")
    else:
        report["avg_compression_x"] = 0
        print("\n[结论] 未生成 EpisodeMemory（流程可能跳过或失败）")

    us = read_understandings(AGENT)
    print(f"\nunderstanding 条目数: {len(us)}")
    for u in us.values():
        report["understandings"].append({"subject": u.subject, "content": u.content})
        print(f"  - {u.subject}: {u.content[:50]}")

    out = os.path.join(RESULTS_DIR, "exp3_consolidation.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nsaved -> {out}")

    # 4. 恢复 runtime，避免污染正式存档
    restore_runtime(bak)


if __name__ == "__main__":
    main()