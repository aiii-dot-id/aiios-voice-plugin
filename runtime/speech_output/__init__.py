"""Application-driven, bounded streaming speech output; no microphone ownership."""

from .service import AudioChunk, SpeechOutput, split_text

__all__ = ["AudioChunk", "SpeechOutput", "split_text"]
