"""JSONL 저장소 헬퍼."""
import json
import os

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

POSTS = os.path.join(DATA, "posts.jsonl")
METRICS = os.path.join(DATA, "metrics.jsonl")
SWIPE = os.path.join(DATA, "swipe.jsonl")


def read(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def append(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_all(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def upsert(path, row, key="id"):
    """있으면 병합(기존 수동 태그 보존), 없으면 추가."""
    rows = read(path)
    for i, r in enumerate(rows):
        if r.get(key) == row.get(key):
            merged = dict(row)
            merged.update({k: v for k, v in r.items() if v not in (None, "", [])})
            rows[i] = merged
            write_all(path, rows)
            return False
    append(path, row)
    return True
