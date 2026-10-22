# video-autoedit — SNS 숏폼 자동 편집기

원본 영상을 넣으면 **분석 → 기획 → 진단 → 렌더링**을 자동으로 수행해 Reels/Shorts/TikTok용 세로 영상과
편집 리포트(진단표·업로드 패키지·검수 체크리스트)를 만든다.

```
원본.mp4 ─▶ analyze ─▶ planner(Claude | 규칙) ─▶ pacing(보정) ─▶ scorecard(진단·자동수정) ─▶ render(ffmpeg)
            무음/음량피크/장면전환/       후킹·컷·강조·효과음·         단어경계 스냅·정지구간    3초룰·컷리듬·자막·CTA·      9:16 리프레이밍·줌펀치·
            대표프레임/음성인식          자료화면·캡션 기획           줌 보충·과밀 제거         규정·사실검증 플래그        자막·효과음·BGM덕킹·-14LUFS
```

## 설치

```bash
# ffmpeg 6.x (libass 포함) + 한글 폰트 (예: Ubuntu)
sudo apt install ffmpeg fonts-noto-cjk
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...   # 선택: 없으면 규칙 기반 기획으로 동작
```

## 사용법

```bash
# 영상마다 따로 편집, Reels+Shorts 두 버전
python -m autoedit run 촬영1.mp4 촬영2.mp4 --platform reels,shorts

# 여러 컷을 한 편으로 + 의도 지정 + 자료화면 폴더 + 협찬
python -m autoedit run a.mp4 b.mp4 --mode merge --topic "아침 루틴 3가지" \
  --goal conversion --industry beauty --cta "프로필 링크에서 상담 신청" \
  --assets ./자료 --bgm ./bgm_licensed.mp3 --sponsored

# 기획서만 먼저 → plan.json 직접 수정 → 다시 렌더링 (권장 워크플로)
python -m autoedit run a.mp4 --plan-only
python -m autoedit render out/a/reels/plan.json
```

출력: `out/<영상>/<플랫폼>/` 아래 `<영상>_<플랫폼>.mp4`, `.cover.jpg`, `report.md`, `plan.json`.
자막 오타는 `out/<영상>/_analysis/<원본>/transcript.json`을 고치고 다시 실행하면 반영된다.

| 기능 | 동작 |
|---|---|
| 후킹 | 최고점 구간(질문·반전·숫자·음성 에너지)을 맨 앞 0~3.5초로 이동(콜드 오픈) + 상단 타이틀 |
| 컷편집 | 무음 0.35초↑ 자동 점프컷, 인사·말더듬 구간 제외, 단어 중간 컷 방지 |
| 강조 | 핵심 단어 시점 줌 펀치, 자막 키워드 노란색, 정지 구간이 플랫폼 기준 초과 시 줌 자동 보충 |
| 효과음 | 전환 whoosh / 강조 pop / 수치 ding / 후킹 boom — 신호 합성(저작권 무관), `--sfx-dir`로 교체 |
| 자료화면 | 수치·나열 문장 → 텍스트 카드, `--assets` 파일명이 대사 키워드와 맞으면 이미지/영상 삽입 |
| 규격 | 1080×1920, 30fps, H.264/AAC, 가로 원본은 블러 배경(`--reframe crop` 가능), -14 LUFS 보정 |
| 규정 | 최상급·효능단정·비교·부작용 부정 표현 탐지, 업종별 규칙, 협찬 시 #유료광고 첫 줄·화면 배지 강제 |

## 진단 기준의 출처

`huchan-content-feedback` 스킬의 콘텐츠 진단 기준을 영상에 맞게 바꿨다. 심각도(상/중/하)와 리포트 형식은
스킬을 따랐다. 규정 규칙은 `huchan-sales-proposal`의 `compliance-by-industry.md`에서 가져왔다.

| 스킬 기준 | 영상 진단 항목 (`scorecard.py`) |
|---|---|
| 3초 룰 / 후킹 | 첫 컷이 hook인가, 4초 이내인가, 첫 발화 0.8초 이내, 후킹 구간 에너지 |
| 체류시간 설계 | 화면 변화 없는 최장 구간, 평균 컷 길이, 권장 길이 |
| 텍스트 가독성·정보 위계 | 자막 글자수, 후킹 타이틀 15자 |
| CTA·참여 유도 | 마지막 3초 CTA, 저장·공유용 정보 카드, 해시태그 구성 |
| 규정 리스크·사실 검증 | `compliance.py` + 수치 표현 [사실 검증 필요] |

플랫폼 수치에는 `[원리]`(공식 발표)와 `[경험칙]`(현장 추정치, A/B 재검증 대상) 라벨을 붙였다(`platforms.py`).
- YouTube Shorts 3분: 2024-10 발표 ([Engadget](https://engadget.com/entertainment/youtube/youtube-shorts-can-now-run-up-to-three-minutes-160002081.html))
- Instagram Reels 3분: 2025-01 Mosseri 발표 ([Social Media Today](https://www.socialmediatoday.com/news/instagram-officially-expands-reels-length-3-minutes/737766/))
- Instagram 핵심 신호(시청시간·도달 대비 좋아요·도달 대비 공유): Mosseri 2025-01 ([Social Media Today](https://www.socialmediatoday.com/news/instagram-shares-algorithm-insights-2025/738034/))
- -14 LUFS는 YouTube가 공식 문서화하지 않은 통용 기준이라 `[경험칙]`으로 분류했다.

**진단 점수는 편집 구조의 결함을 보는 점수이고, 조회수 예측이 아니다.** 소재의 매력, 타겟 적합성, 차별점은 리포트 6번 체크리스트로 사람이 판단한다.

## 테스트

```bash
python -m pytest -q tests          # 합성 영상으로 분석·기획·규정·렌더링 E2E
AUTOEDIT_SKIP_RENDER=1 python -m pytest -q tests   # 렌더링 제외
```
