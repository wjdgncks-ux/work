#!/usr/bin/env python3
"""내 게시물 + 인사이트 수집 -> data/posts.jsonl, data/metrics.jsonl

사용:
  python3 scripts/collect.py --account huchan_main
  python3 scripts/collect.py --account huchan_main --since 2026-09-01

자동화 구간 #2 (100%). 매일 1회 예약 실행 대상.
"""
import argparse
import datetime as dt
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import store  # noqa: E402
import threads_api as api  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))

# 사람이 채우는 실험 변수. 자동 수집 시 빈 값으로 두고 태깅 단계에서 채운다.
TAG_FIELDS = {
    "hook_type": None, "format": None, "topic": None,
    "cta_type": None, "has_number": None, "has_case": None,
    "experiment_id": None, "is_control": False, "utm_campaign": None,
}


def length_band(text):
    n = len(text or "")
    return "s" if n <= 200 else ("m" if n <= 400 else "l")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--since", help="YYYY-MM-DD")
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()

    _, token = api.creds(args.account)
    media = api.list_media(args.account, since=args.since, limit=args.limit)
    now = dt.datetime.now(KST)
    new_posts = 0

    for m in media:
        published = dt.datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00")).astimezone(KST)
        post = {
            "id": m["id"],
            "account": args.account,
            "permalink": m.get("permalink"),
            "published_at": published.isoformat(),
            "text": m.get("text", ""),
            "media_type": m.get("media_type"),
            "length_band": length_band(m.get("text")),
            "posted_hour_kst": published.hour,
            **TAG_FIELDS,
        }
        if store.upsert(store.POSTS, post):
            new_posts += 1

        try:
            ins = api.media_insights(m["id"], token)
        except api.ThreadsError as e:
            print(f"  [skip insights] {m['id']}: {e}", file=sys.stderr)
            continue

        age = round((now - published).total_seconds() / 3600, 1)
        store.append(store.METRICS, {
            "post_id": m["id"],
            "account": args.account,
            "collected_at": now.isoformat(),
            "age_hours": age,
            **{k: ins.get(k, 0) for k in ("views", "likes", "replies", "reposts", "quotes")},
        })

    print(f"{args.account}: 게시물 {len(media)}건 처리 (신규 {new_posts}), 성과 스냅샷 기록 완료")

    try:
        lim = api.publishing_limit(args.account)
        q = (lim.get("data") or [{}])[0]
        print(f"  발행 쿼터: {q.get('quota_usage')}/{(q.get('config') or {}).get('quota_total')} (24h)")
    except api.ThreadsError as e:
        print(f"  [쿼터 확인 실패] {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
