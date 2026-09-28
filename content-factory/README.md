# Content Factory · MVP

클라이언트 1곳 · 콘텐츠 1편으로 "주제 → 원고 → QA → 카드뉴스 → 디자인"이 한 번에 도는지 검증하는 파이프라인.

```
brand-bible.md ─► 01-brief ─► 02-draft ─► 03-qa ─► 04-cardnews-spec ─► Canva 초안
   (기억)          (기획)       (작성)      (검수)      (파생)              (디자인)
                                             │
                                  '상' 이슈는 원고에 즉시 반영
```

실행: Claude Code에서 `content-factory 스킬로 널위한GYM M-31 블로그 돌려줘`
스킬 정의: `.claude/skills/content-factory/SKILL.md`

## 폴더

```
clients/
  gym-for-you/            널 위한 GYM
    brand-bible.md        브랜드 기억 (드라이브 원자료 요약 + 확인 대기 목록)
    runs/
      001-o-leg-blog/     첫 실행: O다리 블로그 → 카드뉴스 8장
```

## Run 001 결과 요약

| 단계 | 결과 |
|---|---|
| 브리프 | M-24 지역키워드 포스팅, 원천 = 1차분 릴스 1 |
| 원고 | 제목 공식·5단 구조·이미지 5슬롯 충족 |
| QA | '상' 3건 발견 → 원고 v2 반영. **원천(1차분 릴스 1·캡션 1)의 기전 설명 오류도 발견** |
| 카드뉴스 | 8장 스펙 + 캡션 + 촬영 리스트 |
| Canva | `runs/001-o-leg-blog/05-output.md` 참조 |

## 사람이 해야 하는 것 (자동화 대상 아님)

- 한진: 검사 순서·기전 설명 확인, 촬영
- 정후찬: 키워드 검색량 실측, 발행
