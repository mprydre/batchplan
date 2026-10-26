"""batchplan —— 把批量推理任务排成一张可执行的排程。

设计原则：
1. **排错了不花钱**。只做排程，不真的发起请求。
2. **并发数是算出来的**，不是拍脑袋定的。
3. **限流与重试都得算进约束**。忽略退避的排程一上线就会撞墙。

仅依赖 Python 标准库。

用法:
    python batchplan.py --help
    python batchplan.py --tasks 10000 --rps 20 --concurrency 8 --cost-per-call 0.002
    python batchplan.py --tasks 10000 --json
"""

from __future__ import annotations

import argparse
import json
import math
import sys

# ---------------------------------------------------------------------------
# 约束模型
# ---------------------------------------------------------------------------

DEFAULT_LIMITS = {
    # 持续速率上限（请求/秒）
    "rps": 20,
    # 突发额度：允许短时间超速的额度（以「请求」为单位）
    "burst": 40,
    # 并发上限（同时在途的请求数）
    "max_concurrency": 8,
    # 单次请求平均耗时（秒）
    "avg_latency": 2.0,
    # 失败率（0~1），用于估算重试量
    "failure_rate": 0.02,
    # 重试次数上限
    "max_retries": 3,
    # 退避基数（秒），实际退避 = base * 2^n
    "backoff_base": 1.0,
    # 退避上限（秒）
    "backoff_cap": 30.0,
    # 单次调用成本（美元）
    "cost_per_call": 0.002,
}


def compute_concurrency(limits: dict) -> dict:
    """算出实际可用的并发数。

    并发受两个约束：客户端自己设的上限，和速率上限隐含的上限
    （Little's Law：并发 = 速率 × 延迟）。取小的那个。
    """
    rps = limits["rps"]
    latency = limits["avg_latency"]
    client_cap = limits["max_concurrency"]
    rate_cap = math.ceil(rps * latency)

    effective = max(1, min(client_cap, rate_cap))
    return {
        "effective": effective,
        "client_cap": client_cap,
        "rate_cap": rate_cap,
        "binding": "客户端上限" if client_cap <= rate_cap else "速率上限",
        "explanation": (
            f"速率 {rps}/s × 延迟 {latency}s = 需要 {rate_cap} 个并发才跑满速率；"
            f"客户端上限是 {client_cap}，所以实际用 {effective}"
            f"（受{'客户端上限' if client_cap <= rate_cap else '速率上限'}约束）"
        ),
    }


def compute_plan(task_count: int, limits: dict) -> dict:
    """排一张批处理排程。"""
    conc = compute_concurrency(limits)
    eff = conc["effective"]

    rps = limits["rps"]
    latency = limits["avg_latency"]
    failure = limits["failure_rate"]
    max_retries = limits["max_retries"]

    # 基础耗时（按速率上限）
    base_duration = task_count / rps if rps else 0.0

    # 重试带来的额外耗时
    expected_calls = 0.0
    retries_time = 0.0
    for attempt in range(max_retries + 1):
        p = (failure ** attempt) * (1 - failure) if attempt == 0 else (failure ** attempt)
        expected_calls += p
        if attempt > 0:
            backoff = min(limits["backoff_cap"],
                          limits["backoff_base"] * (2 ** (attempt - 1)))
            retries_time += p * backoff

    # 速率上限与并发上限谁更紧
    throughput_rps = min(rps, eff / latency if latency else rps)

    total_duration = max(base_duration, (task_count * expected_calls) / throughput_rps) \
        if throughput_rps else 0.0
    total_duration += retries_time

    # 突发额度能省多少时间
    burst_saving = min(limits["burst"], task_count) / throughput_rps \
        if throughput_rps else 0.0

    total_cost = task_count * expected_calls * limits["cost_per_call"]
    effective_cost = task_count * limits["cost_per_call"]

    # 排程分段：按突发额度分批
    batches: list[dict] = []
    remaining = task_count
    batch_no = 1
    burst = limits["burst"]
    while remaining > 0:
        size = min(burst, remaining)
        duration = size / throughput_rps if throughput_rps else 0.0
        batches.append({
            "batch": batch_no,
            "tasks": size,
            "duration": round(duration, 3),
            "start_offset": round(sum(b["duration"] for b in batches), 3),
        })
        remaining -= size
        batch_no += 1
        if batch_no > 10000:  # 防御：任务数异常大时别无限循环
            break

    return {
        "task_count": task_count,
        "limits": limits,
        "concurrency": conc,
        "throughput": {
            "rps_limit": rps,
            "effective_rps": round(throughput_rps, 3),
            "binding": "并发上限" if eff / latency <= rps else "速率上限",
        },
        "timing": {
            "base_duration": round(base_duration, 3),
            "total_duration": round(total_duration, 3),
            "retry_overhead": round(retries_time, 3),
            "burst_saving": round(burst_saving, 3),
            "expected_calls": round(expected_calls, 4),
            "expected_retries": round(task_count * (expected_calls - 1), 1),
        },
        "cost": {
            "total": round(total_cost, 4),
            "without_retries": round(effective_cost, 4),
            "retry_overhead": round(total_cost - effective_cost, 4),
            "cost_per_call": limits["cost_per_call"],
        },
        "batches": batches,
        "batch_count": len(batches),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def render(plan: dict) -> str:
    out: list[str] = []
    lim = plan["limits"]
    conc = plan["concurrency"]
    t = plan["timing"]
    c = plan["cost"]

    out.append("批量推理排程")
    out.append("=" * 64)
    out.append(f"任务数 {plan['task_count']}")
    out.append("")
    out.append("约束：")
    out.append(f"  速率上限   {lim['rps']}/s")
    out.append(f"  突发额度   {lim['burst']} 请求")
    out.append(f"  并发上限   {lim['max_concurrency']}")
    out.append(f"  平均延迟   {lim['avg_latency']}s")
    out.append(f"  失败率     {lim['failure_rate']:.1%}（最多重试 {lim['max_retries']} 次）")
    out.append("")

    out.append("并发：")
    out.append(f"  实际可用 {conc['effective']}　（受{conc['binding']}约束）")
    out.append(f"  {conc['explanation']}")
    out.append("")

    out.append("吞吐与耗时：")
    out.append(f"  有效速率   {plan['throughput']['effective_rps']}/s"
               f"（受{plan['throughput']['binding']}约束）")
    out.append(f"  基础耗时   {t['base_duration']}s")
    out.append(f"  重试额外   {t['retry_overhead']}s"
               f"（预期重试 {t['expected_retries']} 次）")
    out.append(f"  总耗时     {t['total_duration']}s")
    if t["burst_saving"]:
        out.append(f"  突发额度可省 {t['burst_saving']}s")
    out.append("")

    out.append("成本：")
    out.append(f"  基础成本   ${c['without_retries']:.4f}")
    out.append(f"  重试额外   ${c['retry_overhead']:.4f}")
    out.append(f"  合计       ${c['total']:.4f}")
    out.append("")

    out.append(f"排程分段（共 {plan['batch_count']} 批）：")
    shown = plan["batches"][:10]
    for b in shown:
        out.append(f"  第 {b['batch']:>4} 批  起 {b['start_offset']:>9.3f}s  "
                   f"{b['tasks']:>6} 任务  耗时 {b['duration']:.3f}s")
    if len(plan["batches"]) > 10:
        out.append(f"  …（还有 {len(plan['batches']) - 10} 批）")
    out.append("")
    out.append("注：本工具只排程，不发起任何请求。排错了不花钱。")
    return "\n".join(out).rstrip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="batchplan",
        description="把批量推理任务排成一张可执行的排程（仅依赖标准库）",
        epilog=(
            "示例：\n"
            "  python batchplan.py --tasks 10000 --rps 20 --concurrency 8\n"
            "  python batchplan.py --tasks 10000 --cost-per-call 0.002 --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--tasks", type=int, required=True, help="任务总数")
    ap.add_argument("--rps", type=float, default=DEFAULT_LIMITS["rps"],
                    help="速率上限（请求/秒）")
    ap.add_argument("--burst", type=int, default=DEFAULT_LIMITS["burst"],
                    help="突发额度（请求）")
    ap.add_argument("--concurrency", type=int,
                    default=DEFAULT_LIMITS["max_concurrency"], help="并发上限")
    ap.add_argument("--latency", type=float, default=DEFAULT_LIMITS["avg_latency"],
                    help="平均单次耗时（秒）")
    ap.add_argument("--failure-rate", type=float,
                    default=DEFAULT_LIMITS["failure_rate"], help="失败率")
    ap.add_argument("--max-retries", type=int,
                    default=DEFAULT_LIMITS["max_retries"], help="重试次数上限")
    ap.add_argument("--cost-per-call", type=float,
                    default=DEFAULT_LIMITS["cost_per_call"], help="单次调用成本（美元）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    if args.tasks <= 0:
        print("任务数必须是正数。", file=sys.stderr)
        return 2
    if args.rps <= 0 or args.concurrency <= 0:
        print("速率与并发必须是正数。", file=sys.stderr)
        return 2
    if not (0 <= args.failure_rate < 1):
        print("失败率必须在 0 到 1 之间。", file=sys.stderr)
        return 2

    limits = dict(DEFAULT_LIMITS)
    limits.update({
        "rps": args.rps,
        "burst": args.burst,
        "max_concurrency": args.concurrency,
        "avg_latency": args.latency,
        "failure_rate": args.failure_rate,
        "max_retries": args.max_retries,
        "cost_per_call": args.cost_per_call,
    })

    plan = compute_plan(args.tasks, limits)

    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    print(render(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
