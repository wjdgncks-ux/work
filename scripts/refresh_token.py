#!/usr/bin/env python3
"""장기 액세스 토큰(60일) 갱신. 월 1회 예약 실행 권장.

  python3 scripts/refresh_token.py --account huchan_main
"""
import argparse
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import threads_api as api  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    args = ap.parse_args()
    _, token = api.creds(args.account)
    res = api.get("/refresh_access_token", token, grant_type="th_refresh_token")
    print(f"새 토큰 (만료 {res.get('expires_in', 0) // 86400}일 뒤):\n{res['access_token']}")
    print(f"\n.env 의 THREADS_TOKEN_{args.account.upper()} 를 위 값으로 교체하라.")


if __name__ == "__main__":
    main()
