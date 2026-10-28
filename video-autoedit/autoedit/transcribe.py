"""음성 인식(faster-whisper, 단어 단위 타임스탬프). 미설치 시 None을 돌려준다."""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field

from .media import load_audio


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Sentence:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    language: str
    sentences: list[Sentence]
    kind: str = "speech"  # speech=음성 인식(대사), visual=말 없는 영상에 화면을 보고 만든 자막(visual_captions.py)

    def words(self) -> list[Word]:
        return [w for s in self.sentences for w in s.words]

    def text_between(self, start: float, end: float) -> str:
        return " ".join(w.text for w in self.words() if w.start >= start - 0.05 and w.end <= end + 0.05).strip()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Transcript":
        sentences = []
        for s in d["sentences"]:
            words = [Word(**w) for w in s.get("words", [])]
            if not words:  # 사람이 문장만 고친 경우: 문장 구간에 단어를 균등 배분
                toks = s["text"].split()
                span = (s["end"] - s["start"]) / max(len(toks), 1)
                words = [Word(s["start"] + i * span, s["start"] + (i + 1) * span, t) for i, t in enumerate(toks)]
            sentences.append(Sentence(s["start"], s["end"], s["text"], words))
        return cls(d.get("language", "ko"), sentences, d.get("kind", "speech"))


def transcribe(path: str, model_size: str = "small", language: str = "ko") -> Transcript | None:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("  · faster-whisper 미설치 → 자막/대사 기반 기획 없이 진행 (pip install faster-whisper)", file=sys.stderr)
        return None
    try:
        model = WhisperModel(model_size, device="auto", compute_type="int8")
    except Exception as exc:  # 모델 다운로드 실패 등
        print(f"  · Whisper 모델 로드 실패({exc}) → 음성 인식 생략", file=sys.stderr)
        return None
    # 소리는 ffmpeg로 직접 풀어 넘긴다. 파일 경로를 주면 faster-whisper가 PyAV로 여는데, PyAV 15 이상에서
    # faster-whisper 1.2.1까지의 av.open(metadata_errors=...) 호출이 실패한다(신고: "unexpected keyword argument
    # 'metadata_errors'"). ffmpeg로 풀면 PyAV 버전과 무관하고, 휴대폰 영상(HEVC 등)도 편집과 같은 디코더로 읽는다.
    audio = load_audio(path, 16000)
    if audio.size == 0:
        return Transcript(language, [])  # 소리 없는 영상
    sentences = []
    try:
        segments, info = model.transcribe(
            audio, language=language, word_timestamps=True, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
        )
        for seg in segments:  # 실제 인식은 여기서 진행된다(지연 실행)
            words = [Word(round(w.start, 3), round(w.end, 3), w.word.strip()) for w in (seg.words or []) if w.word.strip()]
            sentences.append(Sentence(round(seg.start, 3), round(seg.end, 3), seg.text.strip(), words))
    except Exception as exc:
        raise RuntimeError(f"음성 인식(자동 캡션) 실패: {exc} — 급하면 설정·점검에서 '자동 캡션 정확도'를 '사용 안 함'으로 "
                           "바꾸면 자막 없이 편집할 수 있습니다. 프로그램을 업데이트한 뒤 다시 시도하세요") from exc
    return Transcript(info.language or language, sentences)
