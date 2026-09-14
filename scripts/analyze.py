#!/usr/bin/env python3
"""포맷/훅/시간대별 성과 비교 리포트.

사용:
  python3 scripts/analyze.py                        # 72h 기준 전체
  python3 scripts/analyze.py --by hook_type --at 24
  python3 scripts/analyze.py --experiment exp-001
  python3 scripts/analyze.py --csv out.csv

규칙 (docs/measurement-design.md):
  - 평균이 아니라 중앙값으로 비교한다
  - n < 20 이면 '판정보류'로 표시한다
  - 경과시간이 다른 게시물을 섞지 않는다
"""
import argparse
import csv
import statistics as st
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import store  # noqa: E402

MIN_N = 20          # 판정에 필요한 최소 표본
TOLERANCE = 0.5     # age_hours 매칭 허용 배율


def snapshot_at(metrics, target_h):
    """게시물별로 target_h 에 가장 가까운 스냅샷 1건만 남긴다."""
    best = {}
    for m in metrics:
        pid, age = m["post_id"], m.get("age_hours", 0)
        lo, hi = target_h * (1 - TOLERANCE), target_h * (1 + TOLERANCE)
        if not (lo <= age <= hi):
            continue
        prev = best.get(pid)
        if prev is None or abs(age - target_h) < abs(prev["age_hours"] - target_h):
            best[pid] = m
    return best


def engagement_rate(m):
    v = m.get("views") or 0
    if not v:
        return 0.0
    inter = sum(m.get(k, 0) for k in ("likes", "replies", "reposts", "quotes"))
    return round(inter / v * 100, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--by", default="hook_type",
                    help="hook_type|format|topic|length_band|cta_type|posted_hour_kst|account")
    ap.add_argument("--at", type=float, default=72, help="기준 경과시간(h)")
    ap.add_argument("--experiment", help="특정 실험만")
    ap.add_argument("--account")
    ap.add_argument("--csv")
    args = ap.parse_args()

    posts = {p["id"]: p for p in store.read(store.POSTS)}
    snaps = snapshot_at(store.read(store.METRICS), args.at)

    if not posts:
        print("data/posts.jsonl 이 비어 있다. 먼저 scripts/collect.py 를 실행하라.")
        return

    groups = {}
    untagged = 0
    for pid, snap in snaps.items():
        p = posts.get(pid)
        if not p:
            continue
        if args.account and p.get("account") != args.account:
            continue
        if args.experiment and p.get("experiment_id") != args.experiment:
            continue
        key = p.get(args.by)
        if key in (None, ""):
            untagged += 1
            continue
        groups.setdefault(str(key), []).append({**snap, "_ctrl": p.get("is_control", False)})

    if not groups:
        print(f"{args.at}h 시점 스냅샷이 없다. "
              f"태깅되지 않은 게시물 {untagged}건. "
              f"collect.py 실행 후 posts.jsonl 의 '{args.by}' 를 채워라.")
        return

    rows = []
    for key, ms in sorted(groups.items(), key=lambda kv: -st.median([m["views"] for m in kv[1]])):
        views = [m["views"] for m in ms]
        rows.append({
            "group": key,
            "n": len(ms),
            "views_median": int(st.median(views)),
            "views_mean": int(st.mean(views)),
            "views_p90": int(sorted(views)[int(len(views) * 0.9) - 1]) if len(views) >= 10 else "",
            "replies_median": int(st.median([m["replies"] for m in ms])),
            "eng_rate_median": st.median([engagement_rate(m) for m in ms]),
            "control": sum(1 for m in ms if m["_ctrl"]),
            "verdict": "판정가능" if len(ms) >= MIN_N else f"판정보류(n<{MIN_N})",
        })

    w = [max(len(str(r[k])) for r in rows + [{k: k}]) for k in rows[0]]
    hdr = "  ".join(k.ljust(w[i]) for i, k in enumerate(rows[0]))
    print(f"\n기준: 발행 후 {args.at}h · 분류축: {args.by}"
          + (f" · 실험: {args.experiment}" if args.experiment else ""))
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print("  ".join(str(v).ljust(w[i]) for i, v in enumerate(r.values())))

    total_n = sum(r["n"] for r in rows)
    decidable = [r for r in rows if r["n"] >= MIN_N]
    print(f"\n표본 {total_n}건 · 판정가능 그룹 {len(decidable)}/{len(rows)}")
    if untagged:
        print(f"경고: 태그 미입력 게시물 {untagged}건이 분석에서 제외됐다.")
    if not decidable:
        print(f"경고: 어떤 그룹도 n≥{MIN_N} 을 넘지 못했다. "
              f"지금 내리는 결론은 근거가 없다. 발행을 더 쌓아라.")
    elif len(decidable) < 2:
        print("경고: 비교 대상이 1개뿐이다. 대조군(is_control) 없이는 차이를 해석할 수 없다.")
    if not any(r["control"] for r in rows):
        print("경고: 대조군으로 표시된 게시물이 0건이다. 생존자 편향을 통제하지 못한다.")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.DictWriter(f, fieldnames=list(rows[0]))
            wr.writeheader()
            wr.writerows(rows)
        print(f"\nCSV 저장: {args.csv}")


if __name__ == "__main__":
    main()
