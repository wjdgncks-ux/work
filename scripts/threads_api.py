"""Threads Graph API 최소 클라이언트. 표준 라이브러리만 사용(설치 불필요).

문서: https://developers.facebook.com/docs/threads/
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://graph.threads.net/v1.0"


class ThreadsError(RuntimeError):
    pass


def load_env(path=".env"):
    """.env 를 os.environ 에 로드 (python-dotenv 없이)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def creds(account):
    """계정 키 -> (user_id, token)."""
    load_env()
    suffix = account.upper()
    token = os.environ.get(f"THREADS_TOKEN_{suffix}")
    user_id = os.environ.get(f"THREADS_USER_ID_{suffix}")
    if not token or not user_id:
        raise ThreadsError(
            f"'{account}' 자격증명 없음. .env 에 "
            f"THREADS_TOKEN_{suffix} / THREADS_USER_ID_{suffix} 를 설정하라."
        )
    return user_id, token


def _request(method, path, params=None, retries=3):
    params = dict(params or {})
    url = f"{BASE}{path}"
    data = None
    if method == "GET":
        url += "?" + urllib.parse.urlencode(params)
    else:
        data = urllib.parse.urlencode(params).encode()

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, method=method)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            # 4xx 는 재시도해도 같다. 5xx/429 만 백오프.
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise ThreadsError(f"HTTP {e.code} {path}: {body}") from e
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise ThreadsError(f"network error {path}: {e}") from e
    raise ThreadsError(f"unreachable: {path}")


def get(path, token, **params):
    params["access_token"] = token
    return _request("GET", path, params)


def post(path, token, **params):
    params["access_token"] = token
    return _request("POST", path, params)


# ---------- 조회 ----------

MEDIA_FIELDS = "id,media_type,permalink,timestamp,text,shortcode,is_quote_post"
MEDIA_METRICS = "views,likes,replies,reposts,quotes"
USER_METRICS = "views,likes,replies,reposts,quotes,followers_count"


def list_media(account, since=None, limit=100):
    """내 게시물 목록. since: 'YYYY-MM-DD'."""
    user_id, token = creds(account)
    params = {"fields": MEDIA_FIELDS, "limit": limit}
    if since:
        params["since"] = since
    out, path = [], f"/{user_id}/threads"
    while path:
        res = get(path, token, **params)
        out.extend(res.get("data", []))
        nxt = res.get("paging", {}).get("cursors", {}).get("after")
        if not nxt or len(out) >= limit:
            break
        params["after"] = nxt
    return out[:limit]


def media_insights(media_id, token, metrics=MEDIA_METRICS):
    """게시물 단위 인사이트 -> {metric: value}."""
    res = get(f"/{media_id}/insights", token, metric=metrics)
    out = {}
    for row in res.get("data", []):
        vals = row.get("values") or [{}]
        out[row["name"]] = vals[0].get("value", 0)
    return out


def account_insights(account, since, until):
    """계정 단위 인사이트. since/until: unix timestamp(int)."""
    user_id, token = creds(account)
    return get(
        f"/{user_id}/threads_insights", token,
        metric=USER_METRICS, since=since, until=until,
    )


def publishing_limit(account):
    """24시간 이동창 발행 쿼터 확인 (기본 250 posts / 1000 replies)."""
    user_id, token = creds(account)
    return get(
        f"/{user_id}/threads_publishing_limit", token,
        fields="quota_usage,config,reply_quota_usage,reply_config",
    )


# ---------- 발행 ----------

def create_container(account, text, reply_to_id=None, link_attachment=None):
    user_id, token = creds(account)
    params = {"media_type": "TEXT", "text": text}
    if reply_to_id:
        params["reply_to_id"] = reply_to_id
    if link_attachment:
        params["link_attachment"] = link_attachment
    res = post(f"/{user_id}/threads", token, **params)
    return res["id"]


def publish_container(account, creation_id, wait=30):
    """컨테이너 생성 후 30초 대기 권장(Meta 문서)."""
    user_id, token = creds(account)
    if wait:
        time.sleep(wait)
    res = post(f"/{user_id}/threads_publish", token, creation_id=creation_id)
    return res["id"]
