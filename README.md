# 스레드 운영 파이프라인 — 에그글로벌 / 정후찬

조회수가 아니라 **문의**를 목표로 하는 스레드 운영 자동화 구조.
설계 근거는 `docs/measurement-design.md`, 데이터 정의는 `docs/schema.md`.

## 자동화 구간 지도

| # | 단계 | 자동화 | 수단 |
|---|---|---|---|
| 1 | 타인 게시물 수집 | **수동** | `scripts/swipe.py` — 타인 조회수는 API 미제공 |
| 2 | 내 게시물 성과 수집 | **100%** | `scripts/collect.py` (예약) |
| 3 | 패턴 분석 | **100%** | `scripts/analyze.py` (예약) |
| 4 | 기획·초안 생성 | **95%** | 스킬 `huchan-threads-post` |
| 5 | **검수** | **0% — 게이트** | 사람이 `drafts/approved/` 로 이동 |
| 6 | 발행 | **100%** | `scripts/publish.py` (예약) |
| 7 | 전환 집계 | 반자동 | `data/conversions.csv` |

**5번이 유일한 사람 개입 지점이다.** 그 이상 자동화하면 컴플라이언스 리스크를
대행사가 자기 계정에서 먼저 저지르게 된다.

## 설치

의존성 없음(Python 3.9+ 표준 라이브러리만).

```bash
cp .env.example .env   # 토큰·user_id 입력
```

토큰 발급: https://developers.facebook.com/docs/threads/get-started
필요 권한: `threads_basic`, `threads_content_publish`, `threads_manage_insights`

## 일일 루프

```bash
# 아침 — 성과 수집 (예약)
python3 scripts/collect.py --account huchan_main
python3 scripts/collect.py --account eggglobal

# 주 1회 — 패턴 확인
python3 scripts/analyze.py --by hook_type --at 72
python3 scripts/analyze.py --by posted_hour_kst --at 24
python3 scripts/swipe.py --stats

# 기획 — 대화에서 스킬 호출
#   "스레드 초안 3개 뽑아줘"  → drafts/queue/ 에 저장됨

# 검수 — 사람. 통과분만 이동
mv drafts/queue/2026-09-14-001-*.md drafts/approved/

# 발행 (예약)
python3 scripts/publish.py --dry-run
python3 scripts/publish.py
```

## 수동 구간 — 하루 3분

타인 게시물은 눈으로 보고 넣는다. **viral 과 control 을 같은 수로** 넣어야
생존자 편향을 통제할 수 있다. 한쪽만 모으면 "공통점 분석"이 아니라 점(占)이 된다.

```bash
python3 scripts/swipe.py --url <링크> --views 23000 --band viral   --hook-type contrarian
python3 scripts/swipe.py --url <링크> --views 380   --band control --hook-type contrarian
```

## 판정 규칙 — 어기면 전부 무의미

1. 평균이 아니라 **중앙값**
2. 그룹당 **n ≥ 20** 미만은 판정보류
3. **대조군** 없이 결론 금지
4. 경과시간이 다른 게시물을 섞어 비교하지 않음(72h 기준 고정)

`analyze.py` 가 위반 시 경고를 출력한다.

## 한계 (알고 시작할 것)

- **타인 게시물의 조회수는 공식 API로 수집 불가.** insights는 본인 미디어 한정
  ([Sendible](https://www.sendible.com/insights/threads-insights))
- `keyword_search` 는 텍스트만 반환하며 App Review + 데모가 필요하고 24h 2,200쿼리 제한
  ([Meta](https://developers.facebook.com/docs/threads/keyword-search/))
- 발행 쿼터 24시간 250건 / 답글 1,000건
  ([Meta](https://developers.facebook.com/documentation/threads/overview))
- 알고리즘 관련 서술(초기 10분 답글 등)은 **벤더 블로그 관찰이며 Meta 공식 발표가 아니다.**
  가설로만 취급하고 본인 데이터로 검증한다.

## 디렉터리

```
scripts/    수집·분석·발행 (의존성 없음)
data/       posts.jsonl · metrics.jsonl · swipe.jsonl · conversions.csv
data/examples/  동작 확인용 샘플 (실데이터 아님)
drafts/     queue(생성) → approved(검수통과) → published(발행완료)
docs/       측정 설계 · 스키마
.claude/skills/huchan-threads-post/   초안 생성 스킬
```
