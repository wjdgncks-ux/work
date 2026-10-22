"""가짜 VoiceStudio 서버: 실제 VoiceStudio 백엔드와 같은 경로·필드·오류 모양으로 응답한다(외부 의존성 없음).

확인한 원본: debpalash/VoiceStudio backend/api/routers/openai_compat.py(/v1/audio/speech, /v1/audio/transcriptions,
OpenAI식 오류 {"error": {"message", "code"}}), profiles.py(/profiles), engines.py(/engines/tts), main.py(/health).
- moss-tts-nano: 설치 안 된 엔진(400 model_not_available)
- cosyvoice: 라이선스 미수락(403)
- 음성 인식은 첫 두 단어를 붙여서 돌려준다(인식 결과 글자가 원문과 다를 때 원문을 지키는지 확인용).
"""

from __future__ import annotations

import json
import subprocess
import threading
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class State:
    def __init__(self):
        self.profiles = [{"id": "demo01", "name": "데모 목소리", "kind": "design"}]
        self.speech: list[dict] = []
        self.created: list[dict] = []
        self.last_text = ""
        self.lock = threading.Lock()


def _form(handler, body: bytes) -> dict:
    msg = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + handler.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
    out = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        out[name] = part.get_content() if part.get_filename() is None else (part.get_filename(), part.get_payload(decode=True))
    return out


def _wav(seconds: float) -> bytes:
    return subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=f=330:d={seconds:.2f}",
                           "-ar", "24000", "-ac", "1", "-f", "wav", "-"], capture_output=True, check=True).stdout


def make_handler(st: State):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, data, ctype="application/json"):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _err(self, code: int, message: str, err_code: str):
            self._send(code, {"error": {"message": message, "type": "invalid_request_error", "code": err_code}})

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"status": "ok"})
            if self.path == "/profiles":
                return self._send(200, st.profiles)
            if self.path == "/engines/tts":
                return self._send(200, {"active": "omnivoice", "backends": [
                    {"id": "omnivoice", "available": True}, {"id": "voxcpm2", "available": True},
                    {"id": "cosyvoice", "available": True},
                    {"id": "moss-tts-nano", "available": False, "reason": "pip install moss-tts-nano"}]})
            self._send(404, {"detail": "Not Found"})

        def do_DELETE(self):
            pid = self.path.rsplit("/", 1)[1]
            st.profiles = [p for p in st.profiles if p["id"] != pid]
            self._send(200, {"deleted": pid})

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path == "/profiles":
                f = _form(self, body)
                assert f["kind"] == "clone" and f["ref_audio"][1][:4] == b"RIFF", "기준 녹음은 wav 한 개"
                p = {"id": f"p{len(st.created) + 1:03d}", "name": f["name"], "kind": "clone"}
                st.created.append({**p, "bytes": len(f["ref_audio"][1])})
                st.profiles.append(p)
                return self._send(200, p)
            if self.path == "/v1/audio/speech":
                req = json.loads(body)
                st.speech.append(req)
                if req["model"] == "moss-tts-nano":
                    return self._err(400, "Engine 'moss-tts-nano' is not available: not installed", "model_not_available")
                if req["model"] == "cosyvoice":
                    return self._err(403, "Licence not accepted for FunAudioLLM/Fun-CosyVoice3-0.5B-2512. "
                                          "Review and accept it in Model Catalogue.", "model_licence_not_accepted")
                st.last_text = req["input"]
                return self._send(200, _wav(max(0.6, len(req["input"].replace(" ", "")) / 6)), "audio/wav")
            if self.path == "/v1/audio/transcriptions":
                f = _form(self, body)
                assert f["response_format"] == "verbose_json" and f["timestamp_granularities[]"] == "word"
                toks = st.last_text.split()
                if len(toks) > 2:  # 인식 오류 흉내: 첫 두 단어를 붙임
                    toks = [toks[0] + toks[1]] + toks[2:]
                dur = max(0.6, len(st.last_text.replace(" ", "")) / 6)
                span = (dur - 0.2) / max(1, len(toks))
                words = [{"word": t, "start": round(0.1 + i * span, 3), "end": round(0.1 + (i + 1) * span - 0.03, 3)}
                         for i, t in enumerate(toks)]
                return self._send(200, {"text": " ".join(toks), "words": words})
            self._send(404, {"detail": "Not Found"})
    return H


def start(port: int = 0) -> tuple[ThreadingHTTPServer, str, State]:
    st = State()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(st))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}", st
