"""표시·광고 리스크 점검.

출처: huchan-sales-proposal 스킬 references/compliance-by-industry.md, master-source.md 3-0
- 표시·광고의 공정화에 관한 법률: 근거 없는 최상급·단정 표현
- 공정위 '추천·보증 등에 관한 표시·광고 심사지침': 협찬 표기는 본문 첫 줄에 별도 표기
- 의료법 제56조 제2항: 치료경험담, 비교, 부작용 누락, 과장
조문 원문은 반드시 https://www.law.go.kr 에서 최신본 확인. 이 모듈은 '의심 표현 탐지기'이지 법률 판단이 아니다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

COMMON = {
    r"최고(?!급)": "근거 없는 최상급 표현",
    r"최초": "근거 없는 최상급 표현",
    r"(업계|국내|카테고리)?\s*1\s*위|넘버\s*원|No\.?\s*1": "순위 표현은 객관적 근거(출처·기간) 병기 필요",
    r"유일(한|무이)?": "근거 없는 단정 표현",
    r"100\s*%": "단정 수치(정품 100%, 효과 100% 등)",
    r"완치|완벽(하게|히)?\s*(해결|제거|개선)": "효능 단정",
    r"무조건|반드시\s*(효과|빠|낫)|보장(합니다|해요|됨)": "효과 보장 표현",
    r"부작용\s*(이\s*)?(없|zero|제로)": "부작용 누락/부정 표현",
    r"(다른|타사|경쟁)\s*(제품|업체|병원|가게)\s*(보다|대비)": "비교 광고 표현",
    r"최저가|최고가\s*매입": "가격 최상급 표현",
}

INDUSTRY = {
    "medical": {
        r"시술\s*(받|후기)|치료\s*(후기|경험)": "의료법 제56조 ②: 치료경험담 광고 금지 → '상담 받으러 갔다' 수준으로",
        r"통증\s*(이\s*)?(사라|없어)": "치료 효과 오인 우려",
        r"(병원|의원|클리닉)\s*추천": "환자 유인·추천 표현",
    },
    "beauty": {
        r"(기미|주름|여드름|탈모)\s*(가\s*)?(사라|없어|치료|개선\s*보장)": "화장품 효능 단정(기능성 심사 범위 확인)",
        r"재생|세포": "의약품 오인 우려 표현",
    },
    "supplement": {
        r"(질병|암|당뇨|고혈압)\s*(예방|치료|개선)": "건강기능식품의 질병 예방·치료 표현 금지 [law.go.kr 재확인]",
        r"면역력\s*(강화|높)": "인정된 기능성 문구인지 확인 필요",
    },
    "realestate": {
        r"(확정|보장)\s*수익|무조건\s*오른": "투자 수익 보장 표현",
    },
    "saas": {
        r"정확도\s*\d+\s*%": "근거 자료 없는 성능 수치",
    },
}

NUMBER = re.compile(r"\d+(\.\d+)?\s*(%|배|만\s*명|억|천만|위)")
DISCLOSURE = re.compile(r"#?(유료\s*광고|광고|협찬|제품\s*제공)")


@dataclass
class Flag:
    where: str
    text: str
    reason: str
    severity: str  # 상/중/하


def scan_text(where: str, text: str, industry: str = "general") -> list[Flag]:
    flags: list[Flag] = []
    rules = dict(COMMON)
    rules.update(INDUSTRY.get(industry, {}))
    for pattern, reason in rules.items():
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            flags.append(Flag(where, m.group(0).strip(), reason, "상"))
    for m in NUMBER.finditer(text):
        flags.append(Flag(where, m.group(0), "수치 표현: 출처 확인 전 단정 사용 금지 [사실 검증 필요]", "중"))
    return flags


def check_disclosure(sponsored: bool, sns_caption: str) -> list[Flag]:
    """화면 표기(유료광고 배지)는 렌더러가 강제로 넣으므로 여기서는 게시글 캡션만 본다."""
    if not sponsored:
        return []
    out = []
    first_line = (sns_caption.strip().splitlines() or [""])[0]
    if not DISCLOSURE.match(first_line.strip()):  # 첫 줄 맨 앞에 단독 표기
        out.append(Flag("SNS 캡션 첫 줄", first_line[:30], "협찬 콘텐츠: 캡션 첫 줄에 #유료광고 등 별도 표기 필요(해시태그 묶음 속 표기 불가)", "상"))
    return out
