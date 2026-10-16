"""Bounded streaming lifecycle for learned turn-completion evidence.

The semantic model observes audio and may emit completion evidence.  End of
stream is a transport fact and must never be predicted by that model.  This
module joins those two authorities without sampling the model at one arbitrary
deadline: evidence is entered once, held through silence for a bounded period,
revoked by resumed speech or expiry, and resolved only by an explicit terminal
input.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


CompletionEventKind = Literal[
    "completion_entered",
    "completion_held",
    "completion_revoked",
    "stream_terminal",
]
TerminalAction = Literal["yield", "hold"]


@dataclass(frozen=True)
class CompletionEvent:
    sequence: int
    kind: CompletionEventKind
    source_sample: int
    evidence_source_sample: int | None
    reason: str
    terminal_action: TerminalAction | None = None

    def to_dict(self) -> dict[str, int | str | None]:
        return asdict(self)


class CompletionLifecycle:
    """Consume ordered observations and resolve completion at stream end.

    ``maximum_hold_samples`` is deliberately expressed on the source clock.
    Wall-clock scheduling cannot change the semantic lifetime of evidence.
    """

    def __init__(self, *, maximum_hold_samples: int):
        if maximum_hold_samples <= 0:
            raise ValueError("maximum_hold_samples must be positive")
        self.maximum_hold_samples = maximum_hold_samples
        self._last_sample = -1
        self._entered_sample: int | None = None
        self._sequence = 0
        self._terminal = False

    @property
    def completion_active(self) -> bool:
        return self._entered_sample is not None

    def _emit(
        self,
        kind: CompletionEventKind,
        source_sample: int,
        *,
        reason: str,
        terminal_action: TerminalAction | None = None,
        evidence_source_sample: int | None = None,
    ) -> CompletionEvent:
        self._sequence += 1
        return CompletionEvent(
            sequence=self._sequence,
            kind=kind,
            source_sample=source_sample,
            evidence_source_sample=evidence_source_sample,
            reason=reason,
            terminal_action=terminal_action,
        )

    def _advance(self, source_sample: int) -> None:
        if self._terminal:
            raise RuntimeError("completion lifecycle is already terminal")
        if source_sample < 0 or source_sample < self._last_sample:
            raise ValueError("source samples must be nonnegative and monotonic")
        self._last_sample = source_sample

    def observe(
        self,
        *,
        source_sample: int,
        semantic_label: str,
        speech_resumed: bool,
    ) -> CompletionEvent | None:
        """Consume one causal semantic observation.

        Once completion has been entered, later classifier drift during silence
        cannot erase the event.  Causal evidence that speech resumed can.
        """

        self._advance(source_sample)
        if self._entered_sample is not None:
            entered = self._entered_sample
            if speech_resumed:
                self._entered_sample = None
                return self._emit(
                    "completion_revoked",
                    source_sample,
                    evidence_source_sample=entered,
                    reason="speech_resumed",
                )
            if source_sample - entered > self.maximum_hold_samples:
                self._entered_sample = None
                return self._emit(
                    "completion_revoked",
                    source_sample,
                    evidence_source_sample=entered,
                    reason="evidence_expired",
                )
            return self._emit(
                "completion_held",
                source_sample,
                evidence_source_sample=entered,
                reason="bounded_silence_hold",
            )

        if semantic_label == "yield_completion":
            self._entered_sample = source_sample
            return self._emit(
                "completion_entered",
                source_sample,
                evidence_source_sample=source_sample,
                reason="learned_semantic_evidence",
            )
        return None

    def finish(self, *, source_sample: int) -> CompletionEvent:
        """Resolve the stream using explicit transport termination."""

        self._advance(source_sample)
        entered = self._entered_sample
        active = (
            entered is not None
            and source_sample - entered <= self.maximum_hold_samples
        )
        self._terminal = True
        self._entered_sample = None
        return self._emit(
            "stream_terminal",
            source_sample,
            evidence_source_sample=entered,
            reason="explicit_end_of_stream",
            terminal_action="yield" if active else "hold",
        )
