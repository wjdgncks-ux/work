"""실행기: 시작 화면 앱을 자식 프로세스로 띄우고, 업데이트 뒤 '다시 시작' 요청(종료 코드 75)이면 다시 띄운다.

- 업데이트로 구성요소(requirements.txt)가 바뀌었으면 앱이 꺼진 사이에 설치한다(윈도우는 쓰는 중인 파일을 못 바꿈).
- 업데이트 직후 새 버전이 바로 죽으면(시작 실패) 자동으로 이전 버전으로 되돌리고 다시 띄운다.
이 모듈과 updater·settings 외에는 아무것도 불러오지 않는다(무거운 모듈을 쥐고 있으면 업데이트가 막힘).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

from . import updater

CRASH_WINDOW = 25  # 업데이트 뒤 이 시간(초) 안에 비정상 종료하면 시작 실패로 본다


def _write_result(data: dict) -> None:
    (updater.updates_dir() / "result.json").write_text(json.dumps({**data, "at": time.time()}, ensure_ascii=False),
                                                       encoding="utf-8")


def _pending() -> dict | None:
    p = updater.updates_dir() / "pending.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    p.unlink(missing_ok=True)
    return data


def _run(cmd: list[str], **kw) -> int:
    """자식 실행. 실행기가 종료 신호를 받으면 자식도 끝낸다(남은 앱이 포트를 쥐고 있지 않게)."""
    p = subprocess.Popen(cmd, **kw)

    def stop(_sig, _frame):
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
        raise SystemExit(0)

    old = signal.signal(signal.SIGTERM, stop)
    try:
        return p.wait()
    except KeyboardInterrupt:  # Ctrl+C는 같은 창의 자식에게도 간다 → 끝날 때까지 잠깐 기다림
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
        raise
    finally:
        signal.signal(signal.SIGTERM, old)


def supervise(child_args: list[str], run=_run, log=print) -> int:
    """child_args: '-m autoedit app' 뒤에 붙일 인자. 반환: 마지막 자식의 종료 코드."""
    env = {**os.environ, "AUTOEDIT_SUPERVISED": "1"}
    args = list(child_args)
    just_updated = None
    while True:
        started = time.time()
        try:
            code = run([sys.executable, "-m", "autoedit", "app", "--child", *args], env=env, cwd=str(updater.ROOT))
        except KeyboardInterrupt:
            return 0
        if just_updated and code not in (0, updater.RESTART_CODE) and time.time() - started < CRASH_WINDOW:
            log(f"새 버전 {just_updated['to']} 시작에 실패해 {just_updated['from']}(으)로 되돌립니다.")
            try:
                updater.rollback()
                _write_result({"state": "rolled_back", "from": just_updated["to"], "to": just_updated["from"],
                               "message": "새 버전이 시작되지 않아 이전 버전으로 되돌렸습니다"})
            except updater.UpdateError as exc:
                log(f"되돌리기 실패: {exc}")
                return code
            just_updated = None
            args = [a for a in args if a != "--no-browser"] + ["--no-browser"]
            continue
        if code != updater.RESTART_CODE:
            return code
        just_updated = _pending()
        if just_updated and just_updated.get("pip"):
            log("업데이트: 프로그램 구성요소를 설치하는 중입니다(몇 분 걸릴 수 있음)…")
            ok, out = updater.pip_install(updater.ROOT)
            if not ok:
                _write_result({"state": "pip_failed", "to": just_updated["to"],
                               "message": "구성요소 설치에 실패했습니다. 인터넷 연결을 확인하고 설정에서 다시 시도하세요",
                               "detail": out})
        log("다시 시작합니다…")
        args = [a for a in args if a != "--no-browser"] + ["--no-browser"]  # 열려 있는 창이 그대로 이어서 씀
