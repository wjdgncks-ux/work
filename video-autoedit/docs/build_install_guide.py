"""설치 안내문 만들기: docs/install-guide.src.html + docs/img/*.jpg → install-guide.html(인쇄·오프라인용), install-guide.pdf(A4 한 장).

  python docs/build_install_guide.py [--chrome <크롬 경로>] [--page-only <출력.html>]

--page-only: 웹 게시용(문서 머리 없이 내용만)을 따로 쓴다.
"""

from __future__ import annotations

import argparse
import base64
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
IMAGES = {"IMG_MODELS": "models.jpg", "IMG_SETTINGS": "settings.jpg", "IMG_VOICE": "voice.jpg"}


def page() -> str:
    html = (HERE / "install-guide.src.html").read_text(encoding="utf-8")
    for key, name in IMAGES.items():
        data = base64.b64encode((HERE / "img" / name).read_bytes()).decode()
        html = html.replace("{{" + key + "}}", f"data:image/jpeg;base64,{data}")
    return html


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chrome", default=shutil.which("chromium") or shutil.which("google-chrome") or "")
    ap.add_argument("--page-only")
    args = ap.parse_args()
    body = page()
    if args.page_only:
        Path(args.page_only).write_text(body, encoding="utf-8")
    full = ROOT / "install-guide.html"
    full.write_text('<!doctype html>\n<html lang="ko"><head><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1">\n' + body.replace("<article", "</head><body>\n<article", 1)
                    + "</body></html>\n", encoding="utf-8")
    if args.chrome:
        pdf = ROOT / "install-guide.pdf"
        subprocess.run([args.chrome, "--headless=new", "--no-sandbox", "--no-pdf-header-footer", "--force-color-profile=srgb",
                        f"--print-to-pdf={pdf}", full.as_uri()], check=True, capture_output=True)
        print(pdf)
    print(full)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
