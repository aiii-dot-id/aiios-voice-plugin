import hashlib
from dataclasses import asdict, dataclass

from runtime.voice_app.uid import SpeakerTools

from .installed import policy_for
from .snapshot import dump_snapshot, load_snapshot


@dataclass(frozen=True)
class PreparedSnapshot:
    base_sha256: str
    snapshot: bytes
    revision: int
    audio_sha256: tuple[str, ...] = ()
    state: str = "prepared"
    applied: bool = False


class SnapshotSpeakerTools(SpeakerTools):
    def __init__(self, config, *, assets, read_snapshot):
        if not callable(read_snapshot):
            raise TypeError("explicit authoritative snapshot reader required")
        self.config = dict(config)
        self.assets = assets
        self.policy = policy_for(self.config, assets)
        self.read_snapshot = read_snapshot
        self.model = None
        self.pending = None

    async def enroll_recordings(self, *args, **kwargs):
        raise ValueError(
            "snapshot enrollment requires prepare_enrollment and host publication"
        )

    async def prepare_enrollment(self, recordings, *, speaker_id, label):
        self.validate_recordings(recordings)
        return await self.run_worker(
            self._prepare, "enroll", tuple(recordings), speaker_id, label
        )

    async def prepare_change(self, operation, *, speaker_id=None):
        if operation not in {"remove", "reset"}:
            raise ValueError("only remove/reset are snapshot changes")
        if operation == "reset" and speaker_id is not None:
            raise ValueError("reset does not take a speaker identity")
        return await self.run_worker(self._prepare, operation, (), speaker_id, None)

    def execute(self, operation, audio, speaker_id, label):
        if operation not in {"list", "identify"}:
            raise ValueError(
                "snapshot mutations require preparation and host publication"
            )
        if (
            speaker_id is not None
            or label is not None
            or (operation == "list" and audio is not None)
        ):
            raise ValueError("unexpected snapshot read arguments")
        raw = self.read_snapshot()
        with load_snapshot(raw, self.policy) as store:
            if operation == "list":
                result = store.list()
            else:
                vector = self.embedding(audio)
                result = asdict(
                    store.identify(
                        vector.vector, embedding_binding=vector.embedding_binding
                    )
                )
                result.update(
                    audio_sha256=vector.audio_sha256,
                    embedding_binding=vector.embedding_binding,
                )
        return {
            **result,
            "qualification": "development_only",
            "used_for_permissions": False,
        }

    def _prepare(self, operation, recordings, speaker_id, label):
        raw = self.read_snapshot()
        with load_snapshot(raw, self.policy) as store:
            hashes = ()
            if operation == "enroll":
                vectors, hashes = self.recording_vectors(recordings, speaker_id, label)
                store.enroll_many(
                    speaker_id,
                    label,
                    [(v.audio_sha256, v.vector) for v in vectors],
                    embedding_binding=self.policy.embedding_binding,
                )
            elif operation == "remove":
                store.remove(speaker_id)
            elif operation == "reset":
                store.reset()
            else:
                raise ValueError("unknown snapshot preparation")
            replacement = dump_snapshot(store)
            revision = store.list()["revision"]
        return PreparedSnapshot(
            hashlib.sha256(raw).hexdigest(), replacement, revision, tuple(hashes)
        )
