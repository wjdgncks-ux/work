# 데이터 스키마

파일은 전부 JSONL(한 줄 = 한 레코드). 스프레드시트로 열고 싶으면 `analyze.py --csv`.

## data/posts.jsonl — 게시물 메타 (불변 + 태그)

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | str | Threads media ID (PK) |
| `account` | str | `huchan_main` \| `eggglobal` |
| `permalink` | str | 공개 URL |
| `published_at` | str | ISO8601 (KST) |
| `text` | str | 본문 원문 |
| `media_type` | str | TEXT / IMAGE / VIDEO / CAROUSEL_ALBUM |
| `hook_type` | str | references/hooks.md 의 분류값 |
| `format` | str | `single` `list` `story` `qa` `case` `rebuttal` |
| `topic` | str | `place_seo` `blog` `ad_perf` `agency_truth` `compliance` `case_study` |
| `length_band` | str | `s`(≤200) `m`(201-400) `l`(401+) |
| `cta_type` | str | `none` `reply_bait` `profile` `link` |
| `has_number` | bool | 본문에 구체 수치 포함 여부 |
| `has_case` | bool | 실제 사례 인용 여부 |
| `posted_hour_kst` | int | 0-23 |
| `experiment_id` | str\|null | `exp-001` 등 |
| `is_control` | bool | 대조군 여부 |
| `utm_campaign` | str\|null | `th-YYYYMMDD-NNN` |

## data/metrics.jsonl — 성과 시계열 (append only)

| 필드 | 설명 |
|---|---|
| `post_id` | posts.jsonl 의 id |
| `collected_at` | ISO8601 |
| `age_hours` | 발행 후 경과 시간(반올림) |
| `views` `likes` `replies` `reposts` `quotes` `shares` | insights 값 |

같은 게시물을 1h / 24h / 72h 시점에 각각 수집한다. 덮어쓰지 않고 쌓는다.

## data/swipe.jsonl — 타인 게시물 (수동 수집)

| 필드 | 설명 |
|---|---|
| `permalink` `username` `text` | 원문 |
| `observed_views` | **육안으로 본 조회수** (API 불가) |
| `observed_at` | 관찰 시각 |
| `hook_type` `format` `topic` | 동일 분류 체계로 태깅 |
| `band` | `viral`(1만+) \| `control`(1천 이하) — **대조군을 같은 수로 넣어야 의미가 생긴다** |
| `note` | 자유 메모 |

## data/conversions.csv — 전환 (외부 유입)

```
utm_campaign,date,link_clicks,leads,note
```

GA4/폼 데이터를 주 1회 붙여넣는다. 자동화 대상 아님(외부 도구 의존).
