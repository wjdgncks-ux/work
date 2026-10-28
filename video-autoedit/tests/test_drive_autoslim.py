"""구글 드라이브 링크로 가져오기(가짜 gdown) + 끝난 프로젝트 자동 정리."""

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import fake_gdown  # noqa: E402
from fixtures import make_video  # noqa: E402
from test_housekeeping import project, put  # noqa: E402

from autoedit import app as appmod  # noqa: E402
from autoedit import gdrive  # noqa: E402

FOLDER = "1FolderAbcdefghijk"


@pytest.fixture()
def st(tmp_path, monkeypatch):
    fake_gdown.install()
    fake_gdown.reset()
    monkeypatch.setenv("AUTOEDIT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(appmod.settings, "workspace", lambda: tmp_path / "ws")
    return appmod.AppState()


def wait(st, key="drive_job"):
    for _ in range(300):
        if getattr(st, key).get("state") != "running":
            return getattr(st, key)
        time.sleep(0.05)
    raise AssertionError("끝나지 않음")


def test_parse_link():
    assert gdrive.parse_link(f"https://drive.google.com/drive/folders/{FOLDER}?usp=sharing") == ("folder", FOLDER)
    assert gdrive.parse_link(f"https://drive.google.com/drive/u/0/folders/{FOLDER}") == ("folder", FOLDER)
    assert gdrive.parse_link("https://drive.google.com/file/d/1FileAbcdefghijk/view?usp=drive_link") == ("file", "1FileAbcdefghijk")
    assert gdrive.parse_link("https://drive.google.com/open?id=1FileAbcdefghijk") == ("file", "1FileAbcdefghijk")
    with pytest.raises(gdrive.DriveError, match="구글 드라이브 링크가 아닙니다"):
        gdrive.parse_link("https://youtu.be/x")
    with pytest.raises(gdrive.DriveError, match="ID를 찾지"):
        gdrive.parse_link("https://drive.google.com/drive/my-drive")


def test_import_folder_dedupe_and_reuse(st, tmp_path):
    a = make_video(tmp_path / "src" / "a.mp4", seconds=4)
    b = make_video(tmp_path / "src" / "b.mp4", seconds=6)
    doc = put(tmp_path / "src" / "기획.pdf", 10)
    fake_gdown.FOLDERS[FOLDER] = [("1aaaaaaaaaaaa", "20251011_224848.mp4", a),
                                  ("1bbbbbbbbbbbb", "2일차/20251011_224848.mp4", b),  # 하위 폴더, 같은 이름
                                  ("1cccccccccccc", "기획.pdf", doc)]
    url = f"https://drive.google.com/drive/folders/{FOLDER}?usp=sharing"
    st.start_drive(url)
    j = wait(st)
    assert j["state"] == "done" and j["skipped"] == 1 and len(j["items"]) == 2
    names = sorted(i["id"] for i in j["items"])
    assert names == ["20251011_224848 (2).mp4", "20251011_224848.mp4"] and all(i["done"] for i in j["items"])
    assert "4.0초" in j["items"][0]["info"] or "6.0초" in j["items"][0]["info"]
    assert not (st.originals / gdrive.PARTIAL).exists()
    # 같은 링크를 다시 넣으면 새로 받지 않는다
    st.start_drive(url)
    j = wait(st)
    assert len(fake_gdown.CALLS) == 2 and all(i["reused"] for i in j["items"])
    assert "이미 받아 둔 파일" in j["items"][0]["info"]
    # 받은 영상은 업로드한 영상처럼 새 프로젝트에 쓸 수 있다
    assert all((st.originals / n).exists() for n in names)


def test_import_errors(st, tmp_path):
    with pytest.raises(ValueError, match="구글 드라이브 링크"):
        st.start_drive("인스타 그거")
    st.start_drive("https://drive.google.com/drive/folders/1privateFolder123")
    assert "링크가 있는 모든 사용자" in wait(st)["error"]
    fake_gdown.FOLDERS["1OnlyDocsFolder1"] = [("1dddddddddddd", "a.pdf", put(tmp_path / "a.pdf", 5))]
    st.start_drive("https://drive.google.com/drive/folders/1OnlyDocsFolder1")
    assert "영상 파일이 없습니다" in wait(st)["error"]
    fake_gdown.FILES["1quotaFileAbcd"] = ("x.mp4", make_video(tmp_path / "x.mp4", seconds=2))
    st.start_drive("https://drive.google.com/file/d/1quotaFileAbcd/view")
    assert "한도" in wait(st)["error"]
    fake_gdown.FILES["1brokenFileAbc"] = ("broken.mp4", put(tmp_path / "broken.mp4", 3000))
    st.start_drive("https://drive.google.com/file/d/1brokenFileAbc/view")
    j = wait(st)
    assert j["state"] == "error" and "깨졌거나" in j["items"][0]["error"]


def test_auto_slim_finished_projects(st, tmp_path):
    ws = st.workspace
    srcs = [put(ws / "원본영상" / f"{n}.mp4", 100) for n in "abcd"]
    done = project(ws, "me", "a", [srcs[0]])
    archived = project(ws, "me", "b", [srcs[1]])
    editing = project(ws, "me", "c", [srcs[2]])
    recent = project(ws, "me", "d", [srcs[3]])
    st.set_status("me/a/reels", "업로드 완료")
    st.set_archived("me/b/reels", True)
    st.set_status("me/d/reels", "업로드 완료")
    old = time.time() - 8 * 86400
    for p in (done, archived, editing):
        os.utime(p / "project.json", (old, old))
    assert st.auto_slim_days() == 7
    r = st.auto_slim()
    assert r["projects"] == 2 and set(r["names"]) == {"a", "b"}
    for p in (done, archived):
        assert not (p / "work").exists() and (p / "v1.mp4").exists()
        assert abs((p / "project.json").stat().st_mtime - old) < 1  # 정리해도 '최근 수정' 시각은 그대로
    assert (editing / "work").exists() and (recent / "work").exists()  # 편집 중·최근 완료는 그대로
    assert all(s.exists() for s in srcs)  # 원본은 자동으로 지우지 않음
    cards = {p["name"]: p for p in st.projects()}
    assert cards["a"]["slimmed"] and not cards["c"]["slimmed"]
    before = r["freed"]
    assert st.auto_slim()["freed"] == before  # 다시 돌려도 지울 게 없으면 결과 그대로
    st.save_settings({"auto_slim_days": -1})
    st.auto_result = None
    os.utime(recent / "project.json", (old, old))
    assert st.auto_slim() is None and (recent / "work").exists()  # 끄면 안 함
    with pytest.raises(ValueError):
        st.save_settings({"auto_slim_days": 5})


def test_auto_slim_immediately_when_set_to_zero(st):
    ws = st.workspace
    p = project(ws, "wife", "x", [put(ws / "원본영상" / "x.mp4", 100)])
    st.save_settings({"auto_slim_days": 0})
    st.set_status("wife/x/reels", "업로드 완료")
    for _ in range(100):
        if not (p / "work").exists():
            break
        time.sleep(0.05)
    assert not (p / "work").exists() and json.loads((p / "project.json").read_text(encoding="utf-8"))["slimmed"]
