"""Explicit file-based enrollment UI adapter; never gates conversation."""

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from runtime.speaker_identity import IdentityStore, Policy
from runtime.speaker_identity.backend import MAX_WAV_BYTES, WeSpeaker


class SpeakerTools:
    def __init__(self, config, *, assets=None):
        self.config = dict(config)
        self.assets = assets
        if config.get("context", "fixed_windows") not in {
            "fixed_windows",
            "full_utterance",
        }:
            raise ValueError("explicit supported speaker context required")
        if assets is None:
            self.policy = Policy(**json.loads(Path(config["policy"]).read_text()))
            self.database = Path(config["database"])
        else:
            from runtime.speaker_identity.installed import configuration

            self.policy, self.database = configuration(self.config, assets)
        self.model = None
        self.pending = None

    async def call(self, operation, *, audio=None, speaker_id=None, label=None):
        return await self.run_worker(self.execute, operation, audio, speaker_id, label)

    async def enroll_recordings(self, recordings, *, speaker_id, label):
        """Trusted caller supplies three selected recordings after authorization.

        This is an engine composition method, not an SDK control or permission
        check. Cancellation retains actual worker custody like identification.
        """
        self.validate_recordings(recordings)
        return await self.run_worker(
            self.execute_recordings, tuple(recordings), speaker_id, label
        )

    @staticmethod
    def validate_recordings(recordings):
        if not isinstance(recordings, (tuple, list)) or len(recordings) != 3:
            raise ValueError("select exactly three distinct enrollment recordings")
        if any(
            not isinstance(audio, bytes) or not 44 <= len(audio) <= MAX_WAV_BYTES
            for audio in recordings
        ):
            raise ValueError("supply three bounded 16 kHz mono PCM16 WAV recordings")

    async def run_worker(self, function, *arguments):
        if self.pending and not self.pending.done():
            raise BlockingIOError("speaker worker is busy; conversation is independent")
        # Retain this task until the synchronous worker really finishes, even if
        # the HTTP caller goes away. No unbounded queue or duplicate model load.
        task = self.pending = asyncio.create_task(
            asyncio.to_thread(
                function,
                *arguments,
            )
        )
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        return await asyncio.shield(task)

    async def retire(self):
        """Wait for the actual worker, including a cancelled caller's work."""
        if self.pending is not None:
            # The requesting call reports inference/store errors. Retirement
            # must still join that failed call, not replay its error as a second
            # session failure after the UID observation named it unavailable.
            await asyncio.shield(asyncio.gather(self.pending, return_exceptions=True))

    def recording_vectors(self, recordings, speaker_id, label):
        # Compute/validate the complete batch BEFORE opening the write authority.
        # An inference failure cannot leave a one- or two-recording profile.
        from runtime.speaker_identity.identity import unit, valid_id

        valid_id(speaker_id)
        if (
            not isinstance(label, str)
            or not 1 <= len(label) <= 128
            or any(ord(c) < 32 for c in label)
        ):
            raise ValueError("label must be 1..128 printable characters")
        vectors = [self.embedding(audio) for audio in recordings]
        hashes = [vector.audio_sha256 for vector in vectors]
        if len(set(hashes)) != 3:
            raise ValueError("duplicate decoded audio refused within enrollment")
        for vector in vectors:
            if vector.embedding_binding != self.policy.embedding_binding:
                raise ValueError("speaker model and policy bindings differ")
            unit(vector.vector)
        # Also validate the aggregate before creating a previously absent store.
        import numpy as np

        unit(np.sum([unit(vector.vector) for vector in vectors], axis=0))
        return vectors, hashes

    def execute_recordings(self, recordings, speaker_id, label):
        vectors, hashes = self.recording_vectors(recordings, speaker_id, label)
        if not self.database.exists():
            with IdentityStore.create(self.database, self.policy):
                pass
        with IdentityStore(self.database, self.policy) as store:
            result = store.enroll_many(
                speaker_id,
                label,
                [(vector.audio_sha256, vector.vector) for vector in vectors],
                embedding_binding=self.policy.embedding_binding,
            )
        return {
            **result,
            "audio_sha256": hashes,
            "embedding_binding": self.policy.embedding_binding,
            "qualification": "development_only",
            "used_for_permissions": False,
        }

    def embedding(self, audio):
        if not isinstance(audio, bytes) or not 44 <= len(audio) <= MAX_WAV_BYTES:
            raise ValueError("supply a bounded 16 kHz mono PCM16 WAV")
        if self.model is None:
            config = self.config
            if self.assets is not None:
                from runtime.speaker_identity.installed import model_config

                config = model_config(config, self.assets)
            if config.get("context", "fixed_windows") == "full_utterance":
                from runtime.speaker_identity.full_context import FullContextSpeaker

                self.model = FullContextSpeaker(
                    Path(config["source_model"]),
                    provider=config["provider"],
                    lowering=Path(config["lowering"])
                    if config.get("lowering")
                    else None,
                    lowering_manifest_sha256=config.get("lowering_manifest_sha256"),
                )
            else:
                self.model = WeSpeaker(
                    Path(config["source_model"]),
                    Path(config["model"]),
                    Path(config["specialization"]),
                    provider=config["provider"],
                )
        if self.model.binding != self.policy.embedding_binding:
            raise ValueError("speaker model and policy bindings differ")
        # Session audio is already bounded bytes. Never spill utterances into
        # an ambient temp directory: the host grants enrollment storage, not
        # transient recording storage. File-oriented tools retain model.embed.
        return self.model.embed_wav(audio)

    def execute(self, operation, audio, speaker_id, label):
        if operation == "list" and not self.database.exists():
            return {
                "speakers": [],
                "state": "not_created",
                "qualification": "development_only",
            }
        if operation == "enroll" and not self.database.exists():
            self.database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with IdentityStore.create(self.database, self.policy):
                pass  # Creation is part of the explicit enrollment action only.
        with IdentityStore(self.database, self.policy) as store:
            if operation == "list":
                result = store.list()
            elif operation == "remove":
                result = store.remove(speaker_id)
            elif operation == "reset":
                result = store.reset()
            elif operation in {"enroll", "identify"}:
                vector = self.embedding(audio)
                if operation == "enroll":
                    result = store.enroll(
                        speaker_id,
                        label,
                        vector.vector,
                        audio_sha256=vector.audio_sha256,
                        embedding_binding=vector.embedding_binding,
                    )
                else:
                    result = asdict(
                        store.identify(
                            vector.vector,
                            embedding_binding=vector.embedding_binding,
                        )
                    )
                result.update(
                    audio_sha256=vector.audio_sha256,
                    embedding_binding=vector.embedding_binding,
                )
            else:
                raise ValueError("unknown speaker operation")
        return {
            **result,
            "qualification": "development_only",
            "used_for_permissions": False,
        }
