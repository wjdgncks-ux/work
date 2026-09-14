#!/usr/bin/env python3
"""승인된 초안 발행 (자동화 구간 #7).

drafts/approved/ 의 .md 파일만 발행한다. drafts/queue/ 는 절대 건드리지 않는다.
사람 검수를 통과한 것만 approved/ 로 옮기는 것이 유일한 게이트다.

사용:
  python3 scripts/publish.py --dry-run
  python3 scripts/publish.py --account huchan_main
  python3 scripts/publish.py --account huchan_main --file drafts/approved/2026-09-14-001.md

초안 파일 형식:
  ---
  account: huchan_main
  utm_campaign: th-20260914-001
  hook_type: contrarian
  ---
  본문...
"""
import argparse
import datetime as dt
import glob
import os
import shutil
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import threads_api as api  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPROVED = os.path.join(ROOT, "drafts", "approved")
PUBLISHED = os.path.join(ROOT, "drafts", "published")
MAX_LEN = 500  # Threads 본문 상한


def parse_draft(path):
    raw = open(path, encoding="utf-8").read()
    meta, body = {}, raw
    if raw.startswith("---"):
        _, front, body = raw.split("---", 2)
        for line in front.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    return meta, body.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account")
    ap.add_argument("--file")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--wait", type=int, default=30, help="컨테이너 생성 후 대기(초)")
    args = ap.parse_args()

    files = [args.file] if args.file else sorted(glob.glob(os.path.join(APPROVED, "*.md")))
    if not files:
        print("drafts/approved/ 에 발행할 초안이 없다. 검수 후 옮겨라.")
        return

    for path in files:
        meta, body = parse_draft(path)
        account = args.account or meta.get("account")
        if not account:
            print(f"[건너뜀] {os.path.basename(path)}: account 미지정")
            continue
        if not body:
            print(f"[건너뜀] {os.path.basename(path)}: 본문 없음")
            continue
        if len(body) > MAX_LEN:
            print(f"[건너뜀] {os.path.basename(path)}: 본문 {len(body)}자 > {MAX_LEN}자 상한")
            continue

        print(f"\n--- {os.path.basename(path)} [{account}] {len(body)}자 ---")
        print(body[:200] + ("…" if len(body) > 200 else ""))

        if args.dry_run:
            print("[dry-run] 실제 발행하지 않음")
            continue

        try:
            cid = api.create_container(account, body)
            print(f"  컨테이너 {cid} 생성 · {args.wait}초 대기")
            mid = api.publish_container(account, cid, wait=args.wait)
        except api.ThreadsError as e:
            print(f"  [발행 실패] {e}", file=sys.stderr)
            continue

        os.makedirs(PUBLISHED, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
        dest = os.path.join(PUBLISHED, f"{stamp}-{mid}-{os.path.basename(path)}")
        shutil.move(path, dest)
        print(f"  발행 완료 media_id={mid}")
        print(f"  → collect.py 로 성과를 수집하고 posts.jsonl 에 태그를 채워라"
              f" (hook_type={meta.get('hook_type', '?')}, utm={meta.get('utm_campaign', '?')})")


if __name__ == "__main__":
    main()
