---
name: content-factory
description: 에그글로벌 클라이언트 콘텐츠 생산 파이프라인. "○○ 블로그 1편 돌려줘", "콘텐츠 팩토리 실행", "미션 M-xx 원고 만들어줘", "이 주제로 블로그→카드뉴스까지" 요청 시 사용. 브랜드 바이블 확인 → 브리프 → 원고 → QA → 카드뉴스 스펙 → Canva 초안 순서로 산출물을 content-factory/clients/<client>/runs/ 에 남긴다.
---

# Content Factory

## 원칙
- `clients/<client>/brand-bible.md`에 없는 사실은 쓰지 않는다. 필요한 정보가 없으면 `[검증필요]`로 표기하고 9장(확인 대기)에 추가한다.
- 한 번의 실행(run)은 한 폴더에 모든 산출물을 남긴다: `runs/NNN-<slug>/`
- 사람 승인 없이 발행하지 않는다. 파이프라인의 끝은 "발행 전 남은 체크" 목록이다.

## 단계

| # | 산출물 | 방법 |
|---|---|---|
| 0 | brand-bible 확인 | 없으면 드라이브에서 클라이언트 폴더를 검색해 먼저 작성 |
| 1 | `01-brief.md` | 연결 미션 · 원천 소재 · 목표 행동 · 검색의도 5문항 · 차별 포인트 · 제목 후보 3개 |
| 2 | `02-draft.md` | `blog-strategy/01-strategy.md` 5장 표준: 증상→원인→**실패 원인**→적용→조건부 마무리. 이미지 슬롯 `[IMG-n]` 5개 이상. `[원리]/[경험칙]/[검증필요]` 라벨 |
| 3 | `03-qa.md` | `huchan-content-feedback` 스킬 형식으로 검수 + brand-bible 6장 금지어 grep. 심각도 '상'은 원고에 즉시 반영하고 버전 표기 |
| 4 | `04-cardnews-spec.md` | 8장: Hook→문제→원인→핵심→해결×2→조건+차별점→CTA. 헤드라인 18자 이내. 캡션·촬영 리스트 포함 |
| 5 | Canva 초안 | Canva `create-design` (Instagram Post Portrait)에 스펙 문구를 그대로 전달. 링크를 `05-output.md`에 기록 |

## 금지어 검사 명령
```
grep -nE "교정|재활|치료|완치|최고|1위|100%|무조건|예방" 02-draft.md
```
해시태그 줄 외에 걸리면 수정한다.
