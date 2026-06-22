from __future__ import annotations
"""Translation of transcript segments via Claude on Amazon Bedrock.

Design (docs/subtitle-platform-design.md §7):
  * Translate per-segment so each translated line maps 1:1 to a source segment.
  * Reuse the source segment's start/end — only the text changes — so subtitle
    timing is always aligned regardless of target language.
  * Send segments as a numbered batch and require a JSON object keyed by index;
    validate the count and retry on mismatch (split the batch) before failing.

On the temp-account workshop account, Opus is not available and bare model IDs
are rejected — Bedrock must be invoked through a cross-region INFERENCE PROFILE
(the `us.` prefix). Default model is Haiku 4.5 (verified invokable).
"""

import json
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Cross-region inference profile ID (NOT the bare model id) — required on this account.
DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_REGION = "us-east-1"
ANTHROPIC_BEDROCK_VERSION = "bedrock-2023-05-31"

# Max source segments per Bedrock request. Short-video segments are small;
# keeping batches modest bounds output size and makes count-mismatch retries cheap.
DEFAULT_BATCH_SIZE = 40
MAX_TOKENS = 8000


class TranslationError(RuntimeError):
    """Raised when a batch cannot be translated with aligned segment count."""


@dataclass
class TranslationEngine:
    """Translates transcript segments to a target language using Bedrock Claude."""

    model_id: str = DEFAULT_MODEL_ID
    region: str = DEFAULT_REGION
    batch_size: int = DEFAULT_BATCH_SIZE
    _client: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._client is None:
            import boto3

            self._client = boto3.client("bedrock-runtime", region_name=self.region)

    @classmethod
    def from_env(cls) -> "TranslationEngine":
        return cls(
            model_id=os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID),
            region=os.environ.get("AWS_REGION", DEFAULT_REGION),
        )

    # --- public API ----------------------------------------------------------

    def translate_segments(
        self,
        segments: list[dict],
        target_language: str,
        source_language: str | None = None,
    ) -> list[dict]:
        """Translate segments, preserving each segment's start/end timing.

        Returns a new list of {start, end, text} with translated text, exactly
        one per input segment. Raises TranslationError if alignment can't be
        achieved even after splitting batches down to single segments.
        """
        if not segments:
            return []

        out: list[dict] = []
        for batch in _chunk(segments, self.batch_size):
            translated = self._translate_batch(batch, target_language, source_language)
            out.extend(translated)

        if len(out) != len(segments):  # defensive; _translate_batch guarantees per-batch
            raise TranslationError(
                f"segment count mismatch: got {len(out)} for {len(segments)} inputs"
            )
        return out

    # --- internals -----------------------------------------------------------

    def _translate_batch(
        self, batch: list[dict], target_language: str, source_language: str | None
    ) -> list[dict]:
        """Translate one batch; on count mismatch, split and recurse; single
        segment that still fails -> TranslationError."""
        texts = [str(s.get("text", "")) for s in batch]
        try:
            translations = self._invoke(texts, target_language, source_language)
        except Exception as exc:  # noqa: BLE001 - want to retry by splitting
            if len(batch) == 1:
                raise TranslationError(f"translation failed: {exc}") from exc
            translations = None

        if translations is None or len(translations) != len(batch):
            if len(batch) == 1:
                raise TranslationError(
                    "translation returned wrong segment count for a single segment"
                )
            mid = len(batch) // 2
            logger.warning(
                "translation count mismatch for batch of %d; splitting", len(batch)
            )
            left = self._translate_batch(batch[:mid], target_language, source_language)
            right = self._translate_batch(batch[mid:], target_language, source_language)
            return left + right

        return [
            {"start": batch[i].get("start", 0.0), "end": batch[i].get("end", 0.0),
             "text": translations[i]}
            for i in range(len(batch))
        ]

    def _invoke(
        self, texts: list[str], target_language: str, source_language: str | None
    ) -> list[str]:
        """Call Bedrock and return a list of translations aligned to `texts`."""
        body = json.dumps(
            {
                "anthropic_version": ANTHROPIC_BEDROCK_VERSION,
                "max_tokens": MAX_TOKENS,
                "system": _system_prompt(target_language, source_language),
                "messages": [
                    {"role": "user", "content": _user_prompt(texts)},
                ],
            }
        )
        response = self._client.invoke_model(modelId=self.model_id, body=body)
        payload = json.loads(_read_body(response["body"]))
        text = _first_text(payload)
        return _parse_indexed_json(text, len(texts))


# --- prompt + parsing helpers (module-level, easy to unit test) --------------


def _system_prompt(target_language: str, source_language: str | None) -> str:
    src = f" The source language is {source_language}." if source_language else ""
    return (
        "You are a professional subtitle translator for short-form videos."
        f"{src} Translate each numbered source line into {target_language}. "
        "Preserve meaning, tone, and the casual spoken style; keep each "
        "translation concise enough for one subtitle line. Do not merge, split, "
        "reorder, or drop lines. Return ONLY a JSON object whose keys are the "
        "line numbers (as strings) and whose values are the translated strings, "
        "with no commentary, no code fences, and no extra keys."
    )


def _user_prompt(texts: list[str]) -> str:
    numbered = {str(i): t for i, t in enumerate(texts)}
    return (
        "Translate every line. Return a JSON object with exactly these keys: "
        f"{list(numbered.keys())}.\n\n"
        f"Source lines (JSON):\n{json.dumps(numbered, ensure_ascii=False)}"
    )


def _read_body(body) -> str:
    """Bedrock returns a StreamingBody (.read()) ; tests may pass bytes/str."""
    if hasattr(body, "read"):
        body = body.read()
    if isinstance(body, bytes):
        return body.decode("utf-8")
    return body


def _first_text(payload: dict) -> str:
    """Extract the assistant text from a Bedrock Anthropic response."""
    for block in payload.get("content", []):
        if block.get("type") == "text":
            return block.get("text", "")
    return ""


def _parse_indexed_json(text: str, expected: int) -> list[str]:
    """Parse a {"0": "...", "1": "..."} object into an ordered list.

    Tolerates surrounding prose / code fences by extracting the outermost JSON
    object. Returns a list of length `expected`; raises ValueError otherwise.
    """
    obj = _extract_json_object(text)
    if not isinstance(obj, dict):
        raise ValueError("translation response was not a JSON object")
    result: list[str] = []
    for i in range(expected):
        key = str(i)
        if key not in obj:
            raise ValueError(f"translation response missing key {key!r}")
        result.append(str(obj[key]))
    return result


def _extract_json_object(text: str) -> object:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("no JSON object found in translation response")


def _chunk(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
