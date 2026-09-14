#!/usr/bin/env python3
"""타인 게시물 수동 수집 (자동화 불가 구간 #1 — 하루 3분).

타인 게시물의 조회수는 공식 API가 제공하지 않는다. 눈으로 보고 넣는다.
  https://developers.facebook.com/docs/threads/keyword-search/

사용:
  python3 scripts/swipe.py --url <permalink> --views 23000 --band viral
  python3 scripts/swipe.py --url <permalink> --views 400  --band control
  python3 scripts/swipe.py --stats

band 는 반드시 짝을 맞춘다. viral 만 모으면 생존자 편향 그 자체가 된다.
"""
import argparse
import datetime as dt
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import store  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))


def stats():
    rows = store.read(store.SWIPE)
    if not rows:
        print("수집된 게시물 없음.")
        return
    viral = [r for r in rows if r.get("band") == "viral"]
    ctrl = [r for r in rows if r.get("band") == "control"]
    print(f"총 {len(rows)}건 · viral {len(viral)} · control {len(ctrl)}")
    if len(ctrl) < len(viral) * 0.8:
        print(f"경고: 대조군이 부족하다(viral {len(viral)} vs control {len(ctrl)}). "
              f"이 상태의 '공통점 분석'은 생존자 편향이다. control 을 {len(viral) - len(ctrl)}건 더 넣어라.")
    for band, group in (("viral", viral), ("control", ctrl)):
        counts = {}
        for r in group:
            counts[r.get("hook_type") or "(미태깅)"] = counts.get(r.get("hook_type") or "(미태깅)", 0) + 1
        if counts:
            print(f"  {band:8} 훅 분포: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--views", type=int)
    ap.add_argument("--band", choices=["viral", "control"])
    ap.add_argument("--username", default="")
    ap.add_argument("--text", default="")
    ap.add_argument("--hook-type", default="")
    ap.add_argument("--format", default="")
    ap.add_argument("--topic", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    if args.stats:
        stats()
        return
    if not (args.url and args.views is not None and args.band):
        ap.error("--url, --views, --band 은 필수 (또는 --stats)")

    store.append(store.SWIPE, {
        "permalink": args.url,
        "username": args.username,
        "text": args.text,
        "observed_views": args.views,
        "observed_at": dt.datetime.now(KST).isoformat(),
        "band": args.band,
        "hook_type": args.hook_type or None,
        "format": args.format or None,
        "topic": args.topic or None,
        "note": args.note,
    })
    print(f"기록: [{args.band}] {args.views:,}뷰 — {args.url}")
    stats()


if __name__ == "__main__":
    main()
