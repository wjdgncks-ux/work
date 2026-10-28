"""채널(카테고리) 이름 바꾸기·추가·삭제: 사용자 폴더에 저장, 프로그램 파일·기존 프로젝트는 그대로."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_housekeeping import project, put  # noqa: E402

from autoedit import app as appmod  # noqa: E402
from autoedit import profiles, reference  # noqa: E402


@pytest.fixture()
def st(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOEDIT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(appmod.settings, "workspace", lambda: tmp_path / "ws")
    monkeypatch.chdir(tmp_path)
    return appmod.AppState()


def names(st):
    return [(c["key"], c["name"]) for c in st.home()["profiles"]]


def test_default_channels_and_rename(st):
    assert names(st) == [("company", "회사 채널"), ("me", "내 채널"), ("wife", "범상무님 채널")]
    builtin = (profiles.PKG_ROOT / "profiles" / "company.json").read_text(encoding="utf-8")
    st.save_channel({"key": "company", "name": "회사 공식", "handle": "@acme", "style": "demo",
                     "platforms": ["reels", "shorts"], "cta": "프로필 링크에서 확인", "hashtags": "#회사, 신제품"})
    assert names(st)[0] == ("company", "회사 공식")
    assert (profiles.PKG_ROOT / "profiles" / "company.json").read_text(encoding="utf-8") == builtin  # 프로그램 파일은 그대로
    prof = profiles.load_profile("company")
    assert prof.handle == "@acme" and prof.platforms == "reels,shorts" and prof.hashtags == ["#회사", "#신제품"]
    assert prof.brief["cta"] == "프로필 링크에서 확인" and prof.brief["industry"]  # 나머지 기본값 유지
    assert Path(prof.render["assets"]).is_absolute() and prof.render["assets"].endswith("assets/company")
    with pytest.raises(ValueError, match="이미 있습니다"):
        st.save_channel({"key": "me", "name": "회사 공식", "style": "explainer", "platforms": ["reels"]})
    with pytest.raises(ValueError, match="1~30자"):
        st.save_channel({"key": "me", "name": " ", "style": "explainer", "platforms": ["reels"]})


def test_add_use_and_delete_channel(st):
    from autoedit.pipeline import RunOptions, resolve
    key = st.save_channel({"name": "범상무님 서브/채널", "handle": "@sub", "style": "funny", "platforms": ["tiktok"]})
    assert key == "범상무님_서브_채널" and names(st)[-1] == (key, "범상무님 서브/채널")
    prof, brief, _, plats = resolve(RunOptions(profile=key))
    assert prof.name == "범상무님 서브/채널" and brief.style == "funny" and [p.key for p in plats] == ["tiktok"]
    # 지워도 그 채널 프로젝트는 그대로 남고 목록에 보인다
    ws = st.workspace
    project(ws, "wife", "v", [put(ws / "원본영상" / "v.mp4", 10)])
    reference.set_channel_default("wife", "card1")
    st.delete_channel("wife")
    assert "wife" not in dict(names(st)) and reference.channel_default("wife") is None
    assert [p["profile"] for p in st.projects()] == ["wife"]
    with pytest.raises(FileNotFoundError):
        profiles.load_profile("wife")
    # 같은 이름으로 다시 만들면 새 채널(기존 key 재사용 안 함 → 지운 채널 폴더와 섞이지 않음)
    k2 = st.save_channel({"name": "wife", "style": "explainer", "platforms": ["reels"]})
    assert k2 == "wife_2"
    with pytest.raises(ValueError, match="없는 채널"):
        st.delete_channel("wife")


def test_channel_api_and_editor_shows_name(st, tmp_path):
    from autoedit.editor import _channel_name
    st.save_channel({"key": "me", "name": "정후찬", "style": "explainer", "platforms": ["reels"]})
    assert _channel_name("me") == "정후찬" and _channel_name("gone") == "gone" and _channel_name(None) is None
    data = json.loads((profiles.user_dir() / "me.json").read_text(encoding="utf-8"))
    assert data["name"] == "정후찬"
