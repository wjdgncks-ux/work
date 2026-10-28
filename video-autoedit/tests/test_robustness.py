"""실사용에서 터지는 경우: 이상한 파일 이름, 아이폰 회전 메타데이터, 소리 없는 영상, 아주 짧은 영상."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import make_video  # noqa: E402

from autoedit.cli import main  # noqa: E402
from autoedit.media import probe  # noqa: E402
from autoedit.render import ff_path  # noqa: E402

HOSTILE = "작업 'A', B; [C] 한글"


def _ff(*args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


def _run(video: Path, out: Path, capsys, *extra) -> dict:
    rc = main(["run", str(video), "--out", str(out), "--whisper", "none", "--llm", "off", "--platform", "reels", *extra])
    assert rc == 0
    return json.loads(capsys.readouterr().out)[0]


def _frame_rgb(video: Path, t: float, x: int, y: int) -> tuple[int, int, int]:
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1",
                          "-vf", f"crop=2:2:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return tuple(raw[:3])


def test_ff_path_escaping():
    # ffmpeg 문서 예: 필터 그래프 안에서 ' → \\\' , : → \\: , , → \,
    assert ff_path("C:\\Users\\김 대리\\a'b,c.ass") == "C\\\\:/Users/김 대리/a\\\\\\'b\\,c.ass"
    assert ff_path("/x/[y];z") == "/x/\\[y\\]\\;z"


def test_render_with_hostile_paths(tmp_path, capsys):
    src = tmp_path / HOSTILE
    src.mkdir()
    video = make_video(src / "아침 '꿀팁', 촬영.mp4", seconds=12)
    res = _run(video, tmp_path / HOSTILE / "출력", capsys)
    out = Path(res["output"])
    assert out.exists() and probe(out).width == 1080


def test_rotated_iphone_video(tmp_path, capsys):
    """가로로 저장되고 회전 메타데이터로 세로 표시되는 아이폰 영상 → 세로 원본으로 인식, 크롭만(검은 띠 없음)."""
    land = tmp_path / "land.mp4"
    _ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:d=8", "-f", "lavfi", "-i", "sine=f=300:d=8",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(land))
    rot = tmp_path / "iphone.mov"
    _ff("-display_rotation", "90", "-i", str(land), "-c", "copy", str(rot))
    info = probe(rot)
    assert info.display_size == (720, 1280)
    res = _run(rot, tmp_path / "out", capsys, "--reframe", "auto")
    out = Path(res["output"])
    assert (probe(out).width, probe(out).height) == (1080, 1920)
    assert max(_frame_rgb(out, 1.0, 540, 4)) > 40  # 맨 위가 검은 띠가 아님(블러 패딩이 아니라 꽉 찬 크롭)


def test_video_without_audio(tmp_path, capsys):
    silent = tmp_path / "무음.mp4"
    _ff("-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:d=10", "-c:v", "libx264", "-preset", "ultrafast", str(silent))
    res = _run(silent, tmp_path / "out", capsys)
    out = Path(res["output"])
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
                             capture_output=True, text=True).stdout.split()
    assert "audio" in streams  # 업로드 플랫폼이 거부하지 않게 무음 트랙이라도 있어야 함
    report = Path(res["report"]).read_text(encoding="utf-8")
    assert "자막" in report


@pytest.mark.parametrize("seconds", [3])
def test_very_short_video(tmp_path, capsys, seconds):
    video = make_video(tmp_path / "짧은.mp4", seconds=seconds)
    res = _run(video, tmp_path / "out", capsys)
    assert Path(res["output"]).exists() and probe(Path(res["output"])).duration > 0.5


def test_korean_windows_locale(tmp_path, monkeypatch, capsys):
    """한국어 윈도우 + Python 3.13: 텍스트 모드 subprocess가 cp949로 읽는다. 작업 폴더(숏폼편집/원본영상)의 한글이
    ffprobe·ffmpeg 출력에 UTF-8로 섞여 나와도 업로드·분석·내보내기가 깨지지 않아야 한다(실사용 신고: 모든 영상 '읽을 수 없음')."""
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp949")
    from autoedit import app as appmod
    ws = tmp_path / "문서" / "숏폼편집"
    monkeypatch.setattr(appmod.settings, "workspace", lambda: ws)
    monkeypatch.setenv("AUTOEDIT_HOME", str(tmp_path / "home"))
    src = make_video(tmp_path / "20251011_224848.mp4", seconds=6)
    got = appmod.AppState().upload("20251011_224848.mp4", src.read_bytes())
    assert got["duration"] > 5
    video = ws / "원본영상" / got["id"]
    assert probe(video).has_audio
    r = _run(video, ws / "out", capsys)
    out = Path(r["output"])
    assert out.exists() and probe(out).duration > 1  # 분석(무음·장면 감지)·렌더·음량 측정까지 통과


def test_upload_reports_reason(tmp_path, monkeypatch):
    from autoedit import app as appmod
    monkeypatch.setattr(appmod.settings, "workspace", lambda: tmp_path / "ws")
    monkeypatch.setenv("AUTOEDIT_HOME", str(tmp_path / "home"))
    with pytest.raises(ValueError, match="깨졌거나 다 받아지지"):
        appmod.AppState().upload("a.mp4", b"\x00" * 4096)
    assert not list((tmp_path / "ws" / "원본영상").glob("*"))
    monkeypatch.setattr(appmod, "probe", lambda p: (_ for _ in ()).throw(FileNotFoundError("ffprobe")))
    with pytest.raises(ValueError, match="ffprobe"):
        appmod.AppState().upload("b.mp4", b"\x00")
