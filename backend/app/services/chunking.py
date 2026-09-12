"""Token-aware, sentence-boundary chunking over ASR segments plus prompt-side formatting."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.transcript import SegmentDraft


@dataclass
class Chunk:
    """A contiguous transcript slice sized for the LLM's context window."""

    text: str
    start_seconds: float
    end_seconds: float
    token_estimate: int


# Token counting: we intentionally use a whitespace heuristic rather than loading a real
# tokenizer. Rationale: the LLM lives behind Ollama (no in-process tokenizer available), and
# adding an extra tokenizer dep (tiktoken, tokenizers with a pretrained model) is overkill for a
# lightweight local app. The ~4/3 factor approximates the "1 token ~= 0.75 words" rule of thumb
# and biases slightly high so chunks stay comfortably inside the context window.
def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text.split()) * 4 // 3)


_SENTENCE_BOUNDARY = re.compile(r"[.!?]\s+")


def _split_sentences(full_text: str) -> list[tuple[str, int, int]]:
    """Split `full_text` on `[.!?]\\s+`. Returns `(sentence_text, start_char, end_char)`."""
    sentences: list[tuple[str, int, int]] = []
    prev = 0
    for match in _SENTENCE_BOUNDARY.finditer(full_text):
        end = match.end()
        piece = full_text[prev:end].strip()
        if piece:
            sentences.append((piece, prev, end))
        prev = end
    tail = full_text[prev:].strip()
    if tail:
        sentences.append((tail, prev, len(full_text)))
    return sentences


def chunk_segments(
    segments: list[SegmentDraft],
    *,
    target_tokens: int,
    overlap_tokens: int,
) -> list[Chunk]:
    """Group `segments` into sentence-boundary-respecting, token-bounded chunks.

    Consecutive chunks overlap by ~`overlap_tokens` tokens of trailing sentences from the
    previous chunk. Empty input yields an empty list.
    """
    if not segments:
        return []

    # Build a character-indexed concatenation of segment texts, plus a per-character map back
    # to the source segment index so each sentence can be resolved to real timestamps.
    pieces: list[tuple[int, str]] = []
    for i, seg in enumerate(segments):
        text = seg.text.strip()
        if not text:
            continue
        if pieces:
            # Space separator is attributed to the incoming segment - close enough for
            # timestamp resolution and simpler than tracking a "gap" sentinel.
            pieces.append((i, " "))
        pieces.append((i, text))

    if not pieces:
        return []

    full_text = "".join(text for _, text in pieces)
    char_to_seg: list[int] = []
    for seg_idx, text in pieces:
        char_to_seg.extend([seg_idx] * len(text))

    sentences = _split_sentences(full_text)
    if not sentences:
        return []

    sent_tokens = [_estimate_tokens(text) for text, _, _ in sentences]

    def seg_for_char(pos: int) -> int:
        clamped = max(0, min(pos, len(char_to_seg) - 1))
        return char_to_seg[clamped]

    chunks: list[Chunk] = []
    i = 0
    n = len(sentences)
    while i < n:
        j = i
        acc = 0
        while j < n and (j == i or acc + sent_tokens[j] <= target_tokens):
            acc += sent_tokens[j]
            j += 1

        first_char = sentences[i][1]
        last_char = sentences[j - 1][2] - 1
        start_seconds = segments[seg_for_char(first_char)].start_seconds
        end_seconds = segments[seg_for_char(last_char)].end_seconds
        chunk_text = " ".join(sentences[k][0] for k in range(i, j))
        chunks.append(
            Chunk(
                text=chunk_text,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                token_estimate=acc,
            )
        )

        if j >= n:
            break

        if overlap_tokens <= 0:
            next_i = j
        else:
            next_i = j - 1
            tail = sent_tokens[next_i]
            while next_i > i and tail < overlap_tokens:
                next_i -= 1
                tail += sent_tokens[next_i]
            if next_i <= i:
                # Guarantee forward progress even when overlap_tokens > target_tokens.
                next_i = i + 1
        i = next_i

    return chunks


def _hms(seconds: float) -> str:
    total = int(round(max(0.0, seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_with_timestamps(chunk: Chunk, segments: list[SegmentDraft]) -> str:
    """Render `chunk` for a prompt: one line per source segment prefixed with `[HH:MM:SS]`."""
    lines: list[str] = []
    epsilon = 0.01
    for seg in segments:
        if seg.start_seconds < chunk.start_seconds - epsilon:
            continue
        if seg.start_seconds > chunk.end_seconds + epsilon:
            break
        text = seg.text.strip()
        if not text:
            continue
        lines.append(f"[{_hms(seg.start_seconds)}] {text}")
    return "\n".join(lines)
