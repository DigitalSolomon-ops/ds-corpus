"""Claude triage — the second, judgment stage of the significance gate.

Deterministic scoring rejects obvious chaff for free (significance.py). What
survives the floor but isn't a canon hit gets *judged* by Claude Haiku: is this
actually significant, is it primary or derivative, and why. Code fetches and
enforces; Claude reasons. That split is the whole point.

Cost discipline is built in, per the brief:
- **Batch API** (50% discount) — triage is non-interactive.
- **Prompt caching** on the shared rubric — paid once per run, not per doc.
- A hard **per-run USD cap** — exceeding it stops triage and checkpoints;
  unscored records stay pending, never dropped.
- Rejects are written to `_rejected/` **with the rationale**, never deleted.
- An **audit sample** of accept/reject decisions is logged for human review —
  without it the rubric drifts and you never find out.

The Anthropic client is injected, so every test runs offline against a fake.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol

TRIAGE_MODEL = "claude-haiku-4-5"

# Haiku 4.5 list price per million tokens; Batch API halves both. Confirm at
# the pricing page before trusting the cap to the cent — this drives the
# checkpoint, so it errs high rather than low.
_INPUT_USD_PER_MTOK = 1.00
_OUTPUT_USD_PER_MTOK = 5.00
_BATCH_DISCOUNT = 0.5
_EST_OUTPUT_TOKENS = 220          # strict-JSON verdicts are small and bounded

HEAD_WORDS = 1500
TAIL_WORDS = 500

# The shared rubric — cached once per run. Keep it byte-stable across a run so
# the cache actually hits (no timestamps, no per-doc interpolation here).
RUBRIC = """\
You are a rigorous acquisitions editor for a curated open-access library whose \
standard is: works that are revered, foundational, or genuinely useful — not \
merely available. You judge one document at a time and return a strict verdict.

Significance (0-100) means enduring importance to its field: a primary source, \
a foundational text, a highly-cited or field-defining work, or a reference of \
lasting practical value. A competent-but-ordinary paper, a derivative summary, \
an ephemeral note, or promotional/marginal material scores low.

Reward: primary sources; works other works build on; clear authority; lasting \
reference value. Penalize: derivative restatements; thin or promotional \
content; narrow ephemera unlikely to be cited or read in five years.

You are given the document's front-matter and excerpts (opening and closing). \
Judge on evidence in front of you; do not speculate beyond it.

Return ONLY a JSON object, no preamble and no markdown fences:
{"significance": <int 0-100>, "category": "<short kebab-case topic>", \
"is_primary_source": <bool>, "is_derivative": <bool>, \
"rationale": "<one or two sentences, specific to this document>", \
"suggested_tags": ["<tag>", ...]}"""


@dataclass
class TriageRecord:
    doc_id: str
    front_matter: dict          # the fm_fields the pipeline already built
    head_text: str
    tail_text: str


@dataclass
class TriageVerdict:
    doc_id: str
    significance: int
    category: str
    is_primary_source: bool
    is_derivative: bool
    rationale: str
    suggested_tags: list = field(default_factory=list)

    def accepted(self, threshold: int) -> bool:
        return self.significance >= threshold


class TriageError(Exception):
    pass


def build_excerpt(body: str, head_words: int = HEAD_WORDS, tail_words: int = TAIL_WORDS) -> tuple[str, str]:
    """First head_words and last tail_words of the body. If the body is short
    enough that they'd overlap, the head is the whole thing and the tail empty."""
    words = body.split()
    head = " ".join(words[:head_words])
    tail = "" if len(words) <= head_words + tail_words else " ".join(words[-tail_words:])
    return head, tail


def build_user_content(record: TriageRecord) -> str:
    fm = record.front_matter
    meta = {
        "title": fm.get("title"),
        "authors": fm.get("authors"),
        "domain": fm.get("domain"),
        "source_id": fm.get("source_id"),
        "original_year": fm.get("original_year"),
        "tags": fm.get("tags"),
    }
    parts = [
        "FRONT-MATTER:\n" + json.dumps(meta, ensure_ascii=False),
        "\nOPENING:\n" + record.head_text,
    ]
    if record.tail_text:
        parts.append("\nCLOSING:\n" + record.tail_text)
    return "\n".join(parts)


def parse_verdict(doc_id: str, text: str) -> TriageVerdict:
    """Strict JSON only. Tolerate accidental ```json fences but nothing else —
    a verdict we can't parse is an error, not a silent default."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned).strip()
    try:
        data = json.loads(cleaned)
    except (ValueError, TypeError) as e:
        raise TriageError(f"{doc_id}: unparseable triage verdict: {e}: {text[:120]!r}")
    try:
        sig = int(data["significance"])
    except (KeyError, TypeError, ValueError) as e:
        raise TriageError(f"{doc_id}: missing/invalid significance: {e}")
    return TriageVerdict(
        doc_id=doc_id,
        significance=max(0, min(100, sig)),
        category=str(data.get("category", "uncategorized")),
        is_primary_source=bool(data.get("is_primary_source", False)),
        is_derivative=bool(data.get("is_derivative", False)),
        rationale=str(data.get("rationale", "")),
        suggested_tags=list(data.get("suggested_tags", []) or []),
    )


def estimate_cost_usd(records: list[TriageRecord]) -> float:
    """Rough Batch-API cost for triaging `records`. ~4 chars/token; the shared
    rubric is cached so it's counted once, not per record. Deliberately a
    slight over-estimate so the cap trips before real spend overruns it."""
    rubric_tokens = len(RUBRIC) // 4
    input_tokens = rubric_tokens  # cached once for the run
    for r in records:
        input_tokens += len(build_user_content(r)) // 4
    output_tokens = _EST_OUTPUT_TOKENS * len(records)
    cost = (
        input_tokens / 1_000_000 * _INPUT_USD_PER_MTOK
        + output_tokens / 1_000_000 * _OUTPUT_USD_PER_MTOK
    )
    return round(cost * _BATCH_DISCOUNT, 4)


class TriageClient(Protocol):
    def triage(self, records: list[TriageRecord]) -> list[TriageVerdict]: ...


class AnthropicBatchTriageClient:
    """Real triage over the Message Batches API with a cached shared rubric.

    Batch results arrive in any order — keyed by custom_id, never by position.
    The client is constructed lazily so importing this module never requires
    the anthropic package or an API key (tests use the fake below).
    """

    def __init__(self, api_key: str | None = None, model: str = TRIAGE_MODEL,
                 poll_seconds: float = 30.0, sleep=None, max_tokens: int = 512) -> None:
        self.api_key = api_key
        self.model = model
        self.poll_seconds = poll_seconds
        self.max_tokens = max_tokens
        import time
        self._sleep = sleep or time.sleep

    def _client(self):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise TriageError(f"anthropic package not installed: {e}") from e
        return anthropic.Anthropic(api_key=self.api_key) if self.api_key else anthropic.Anthropic()

    def triage(self, records: list[TriageRecord]) -> list[TriageVerdict]:  # pragma: no cover
        if not records:
            return []
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request

        client = self._client()
        shared_system = [{
            "type": "text", "text": RUBRIC,
            "cache_control": {"type": "ephemeral"},   # paid once per run
        }]
        requests = [
            Request(
                custom_id=_safe_custom_id(r.doc_id, i),
                params=MessageCreateParamsNonStreaming(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=shared_system,
                    messages=[{"role": "user", "content": build_user_content(r)}],
                ),
            )
            for i, r in enumerate(records)
        ]
        by_custom_id = {_safe_custom_id(r.doc_id, i): r.doc_id for i, r in enumerate(records)}

        batch = client.messages.batches.create(requests=requests)
        while True:
            got = client.messages.batches.retrieve(batch.id)
            if got.processing_status == "ended":
                break
            self._sleep(self.poll_seconds)

        verdicts: list[TriageVerdict] = []
        for result in client.messages.batches.results(batch.id):
            doc_id = by_custom_id.get(result.custom_id, result.custom_id)
            if result.result.type != "succeeded":
                raise TriageError(f"{doc_id}: batch item {result.result.type}")
            text = next((b.text for b in result.result.message.content if b.type == "text"), "")
            verdicts.append(parse_verdict(doc_id, text))
        return verdicts


def _safe_custom_id(doc_id: str, i: int) -> str:
    return f"{i}-" + re.sub(r"[^A-Za-z0-9_.-]", "_", doc_id)[:60]
