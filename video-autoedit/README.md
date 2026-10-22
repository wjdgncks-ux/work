# video-autoedit — SNS 숏폼 자동 편집기

원본 영상을 넣으면 **분석 → 기획 → 진단 → 렌더링**을 자동으로 수행해 Reels/Shorts/TikTok용 세로 영상과
편집 리포트(진단표·업로드 패키지·검수 체크리스트)를 만든다.

```
원본.mp4 ─▶ analyze ─▶ planner(Claude | 규칙) ─▶ pacing(보정) ─▶ scorecard(진단·자동수정) ─▶ render(ffmpeg)
            무음/음량피크/장면전환/       후킹·컷·강조·효과음·         단어경계 스냅·정지구간    3초룰·컷리듬·자막·CTA·      9:16 리프레이밍·줌펀치·
            대표프레임/음성인식          자료화면·캡션 기획           줌 보충·과밀 제거         규정·사실검증 플래그        자막·효과음·BGM덕킹·-14LUFS
```

## 설치 (각 PC에 한 번)

이 프로그램은 **내 PC에 설치해서 쓰는 방식**이다. 영상은 PC 밖으로 나가지 않고, 외부로 나가는 것은 선택 기능뿐이다:
Claude(편집 기획·대본)와 ElevenLabs(AI 음성·목소리 복제)에 보내는 텍스트·음성 샘플.

| 준비물 | Windows | Mac |
|---|---|---|
| Python 3.11+ | python.org 설치 파일(“Add to PATH” 체크) | `brew install python` |
| ffmpeg | `winget install Gyan.FFmpeg` | `brew install ffmpeg` |
| 한글 폰트 | 기본 포함(맑은 고딕 등 → 프로필 `font` 지정) | 기본 포함 |

```bash
git clone <저장소> && cd video-autoedit
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m autoedit settings --elevenlabs-key <키>      # 선택: AI 음성
export ANTHROPIC_API_KEY=...                           # 선택: Claude 기획·대본(없으면 규칙 기반)
```

음성 인식(자동 캡션)은 첫 실행 때 Whisper 모델을 내려받는다(인터넷 필요, 이후 오프라인 동작).

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

### 수작업 편집기 — CapCut과 같은 화면·조작 (`python -m autoedit edit <프로젝트 폴더>`)

브라우저(127.0.0.1)에서 열린다. 설치할 것 없음(파이썬 표준 라이브러리). CapCut PC판 배치를 그대로 따랐다.

```
┌ 소재 패널 ─────────┬──── 플레이어 ────┬─ 속성 ─┐   상단 오른쪽: [AI 진단 점수] [내보내기]
│미디어 오디오 텍스트 │  완성본 미리보기  │ 비디오 │
│자료 효과 전환 캡션 │ (자막·텍스트·줌·  │ 속도   │
│AI 편집             │  효과음 반영)     │ 텍스트 │
├────────────────────┴─────────────────┴────────┤
│ ↶ ↷ │ 분할 왼쪽삭제 오른쪽삭제 삭제 │ 복제   …  자석 ─●─ 확대 │
│ 효과 / 텍스트 / 캡션 / 자료 / [메인 영상 트랙] / 효과음        │
└──────────────────────────────────────────────────┘
```

- **편집기를 처음 열면 자동 무음 컷이 실제 클립으로 나뉜다** → 타임라인에 보이는 그대로 내보내진다.
- 클립: 클릭 선택, 끌어서 순서 변경(자석), 양 끝을 끌어 길이 조절, 오른쪽 패널에서 역할·배속
- 텍스트·자료·효과·효과음: 왼쪽 패널에서 ＋(재생 헤드 위치에 추가) → 타임라인에서 끌어 이동, 오른쪽 끝을 끌어 길이 조절
- 캡션: 왼쪽 '캡션' 탭에서 오타 수정 → 적용, 또는 타임라인 캡션 블록 선택 후 오른쪽에서 수정
- AI 편집 탭: 스타일 선택 → **AI 재편집**(Ctrl+Z로 되돌리기 가능), AI 진단 이슈 목록
- 미디어/자료 **가져오기**: 영상·이미지를 바로 추가(영상은 자동 캡션까지 분석)
- 자동 저장, 내보내기 창에서 최종(1080p)/빠른 미리보기 선택 → 결과 확인·다운로드
- 원본이 HEVC(아이폰 기본)·4K면 편집용 H.264 사본을 자동 생성(내보내기는 원본 화질)

| 단축키(CapCut 기본값과 동일) | 동작 |
|---|---|
| `Space` | 재생/정지 |
| `Ctrl+B` | 재생 헤드에서 분할 |
| `Q` / `W` | 왼쪽 삭제 / 오른쪽 삭제 |
| `Delete` | 선택 항목 삭제 |
| `Ctrl+Z` / `Ctrl+Shift+Z` | 실행 취소 / 다시 실행 |
| `←` `→` (`Shift`) | 1프레임(1초) 이동 |
| `↑` `↓` | 이전/다음 편집 지점 |
| `Ctrl+=` `Ctrl+-` · `Ctrl+휠` · `Shift+Z` | 타임라인 확대·축소·맞춤 |
| `Ctrl+D` | 복제 |
| `Ctrl+E` | 내보내기 |

CapCut과 다른 점(아직 없음): 화면 전환 효과(디졸브 등), 필터·색보정, 키프레임 애니메이션, 텍스트 글꼴·위치 자유 배치
(텍스트 위치·크기는 플랫폼 안전영역 기준으로 자동). 텍스트·효과는 영상 내용에 붙어 있어서 클립을 옮기면 같이 움직인다.

### 음성 — 자동 캡션 · 나레이션 · AI 음성 · 내 목소리

| 기능 | 방법 | 엔진 |
|---|---|---|
| 영상 속 음성 → 자막 | 자동 편집 때 자동, 편집기 '캡션' 탭 **자동 캡션** | Whisper(내 PC) |
| 나레이션 + 자막 | '음성' 탭에 문장 입력 → **재생 위치에 추가**. 자막은 음성의 단어 타이밍에 맞춰 자동 생성 | ElevenLabs |
| AI 대본 | '음성' 탭 **대본 작성** → 줄별 수정 → **전체 음성 생성** | Claude(없으면 화면 문구로 초안) |
| 텍스트 → 음성 | 텍스트 카드 선택 → '텍스트 음성 변환' 탭 → 읽기 | ElevenLabs |
| AI 음성 | 엔진의 기본 제공 목소리 목록에서 선택(미리 듣기) | ElevenLabs |
| 인물 지정 음성(여러 개) | '음성' 탭 **새 목소리 만들기** → 브라우저에서 녹음 또는 파일 → 동의 체크 → 만들기 | ElevenLabs Instant Voice Cloning |

- 나레이션 동안 원본 소리: 줄이기(약 −14dB)/끄기/유지, 볼륨, 자막 표시 여부를 나레이션마다 지정
- 나레이션이 겹치면 타임라인에서 줄이 나뉘고, 진단이 경고한다
- 같은 문장·목소리는 다시 만들지 않는다(요금 절약, 프로젝트 `narration/` 캐시)
- 명령행: `python -m autoedit voices list|stock|clone|say|delete`

**동의와 표시(필수)**
- 목소리 복제는 **목소리 주인의 동의**가 있어야 만들 수 있게 막아 두었다(동의 기록이 목소리 정보에 남는다).
- AI 음성이 들어간 영상에는 화면에 'AI 음성' 표시가 자동으로 들어간다.
  인공지능기본법(2026-01-22 시행)의 생성형 AI 결과물 표시 의무에 맞춘 것이다. 시행 초기에는 과태료 계도기간이다.
  음성에 대한 구체적인 표시 방식은 과기정통부 「인공지능 투명성 확보 가이드라인」 원문으로 확인할 것.
- 업로드 때 플랫폼의 AI 생성 콘텐츠 표시(YouTube '변경되거나 합성된 콘텐츠' 등)도 체크한다.

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
