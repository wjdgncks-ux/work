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
# 1) 채널 프로필로 자동 편집 (profiles/company.json · me.json · wife.json)
python -m autoedit run 촬영1.mp4 촬영2.mp4 --profile company

# 2) 장르(스타일) 직접 지정: explainer=설명형, funny=유머·재미, demo=제품 시연
python -m autoedit run 브이로그.mp4 --profile wife --style funny

# 3) 수작업 편집기(브라우저) — 자동 편집 결과를 열어 직접 다듬고 렌더링
python -m autoedit edit out/company/촬영1/reels

# 기타
python -m autoedit run a.mp4 --plan-only              # 기획서·리포트만
python -m autoedit render out/me/a/reels/plan.json    # plan.json을 직접 고친 뒤 렌더링(--preview 저화질)
python -m autoedit profiles                           # 프로필 목록
```

출력: `out/<프로필>/<영상>/<플랫폼>/` 아래 `<영상>_<플랫폼>.mp4`, `.cover.jpg`, `report.md`, `plan.json`.

### 스타일(장르)별 차이 — `autoedit/styles.py`

| | 설명형 `explainer` | 유머 `funny` | 제품 시연 `demo` |
|---|---|---|---|
| 후킹 | 질문·숫자·반전 문장 | 가장 큰 리액션(음량 피크) 미리보기 | 결과·완성 장면 미리보기 |
| 무음 컷 | 0.35초↑ 전부 컷(속도감) | 0.8초↑만 컷(펀치라인 앞 '숨' 유지) | 0.6초↑ 컷 + 말 없는 작업 구간 2~4배속 |
| 강조 | 핵심 단어 줌 | 강한 줌 + 플래시 + boing | 약한 줌, STEP 단계 카드 |
| 효과음 | whoosh/pop/ding/boom | whoosh/boing/riser | whoosh/pop/ding/riser |
| 길이 | 플랫폼 권장 | 권장 × 0.8 | 권장 × 1.2 |

### 채널 프로필 — `profiles/*.json`

채널별로 기본 장르·플랫폼·CTA·고정 해시태그·자막 강조색·핸들 워터마크·자료 폴더를 저장한다.
`[ ]` 표시 칸과 `@계정`을 실제 값으로 바꿔 쓰고, 자료 이미지는 `assets/<채널>/`에 넣는다(파일명이 대사 키워드와 맞으면 자동 삽입).
명령행 옵션이 프로필보다 우선한다.

### 수작업 편집기 — `python -m autoedit edit <프로젝트 폴더>`

브라우저(127.0.0.1)에서 열리는 편집 화면. 설치할 것 없음(파이썬 표준 라이브러리).
- **컷**: 원본을 보며 `I`/`O`로 구간 지정 → `A` 클립 추가, `S` 현재 위치에서 자르기, 드래그로 순서 변경, 속도(빨리감기) 입력
- **강조·효과음·자료화면**: 현재 위치에 `E` 줌 / 플래시 / `F` 효과음 / 텍스트 카드 / 자료 이미지 추가, 표에서 시각·종류 수정
- **문구**: 후킹 타이틀·CTA·제목 후보·캡션·해시태그·협찬 표기
- **자막**: 음성 인식 오타 수정, 문장 추가, 시각 조정
- **실시간 진단**: 고칠 때마다 점수·이슈·규정 플래그 갱신(렌더 없이 즉시)
- **확인**: 렌더 없이 '시퀀스 재생' → 저화질 미리보기 렌더 → 최종 렌더
- `Ctrl+S` 저장, `Ctrl+Z` 되돌리기. 원본이 HEVC(아이폰 기본)·4K면 편집용 H.264 프록시를 자동 생성(렌더링은 원본으로)

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

**협찬·이해관계 표기**: 동영상은 시작과 끝에 표기하고 영상 중에도 반복 노출하는 것이 공정위 지침의 해설 방향이라, `--sponsored`면 영상 전체에 '유료광고' 배지를 띄운다.
가족 채널에서 회사 제품을 다룰 때 표기 의무가 어디까지인지는 확인하지 못했다. 공정위 지침 원문(https://www.ftc.go.kr)을 확인하기 전까지는 표기하는 쪽을 기본값으로 둔다.

**진단 점수는 편집 구조의 결함을 보는 점수이고, 조회수 예측이 아니다.** 소재의 매력, 타겟 적합성, 차별점은 리포트 6번 체크리스트로 사람이 판단한다.

## 테스트

```bash
python -m pytest -q tests          # 합성 영상으로 분석·기획·규정·렌더링 E2E
AUTOEDIT_SKIP_RENDER=1 python -m pytest -q tests   # 렌더링 제외
```
