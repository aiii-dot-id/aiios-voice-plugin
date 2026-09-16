"""JSON CLI for standalone UID. No device access or implicit enrollment."""

import argparse
import json
import sqlite3
import sys
import wave
from dataclasses import asdict
from pathlib import Path

from .identity import IdentityStore, Policy, SpeakerIdentityError


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database", type=Path, required=True)
    p.add_argument("--policy", type=Path, required=True)
    p.add_argument(
        "--provider", choices=["coreml", "cuda", "directml", "cpu"], default="coreml"
    )
    # Paths are explicit so an installed component has no workspace dependency.
    p.add_argument("--source-model", type=Path)
    p.add_argument("--model", type=Path)
    p.add_argument("--specialization", type=Path)
    commands = p.add_subparsers(dest="operation", required=True)
    commands.add_parser("create")
    commands.add_parser("list")
    e = commands.add_parser("enroll")
    e.add_argument("speaker_id")
    e.add_argument("--label", required=True)
    e.add_argument("--wav", type=Path, required=True)
    q = commands.add_parser("identify")
    q.add_argument("--wav", type=Path, required=True)
    r = commands.add_parser("remove")
    r.add_argument("speaker_id")
    r = commands.add_parser("reset")
    r.add_argument("--confirm", action="store_true", required=True)
    args = p.parse_args(argv)
    try:
        policy = Policy(**json.loads(args.policy.read_text()))
        if args.operation == "create":
            with IdentityStore.create(args.database, policy) as store:
                result = {"operation": "created", **store.list()}
        else:
            with IdentityStore(args.database, policy) as store:
                if args.operation == "list":
                    result = store.list()
                elif args.operation == "remove":
                    result = store.remove(args.speaker_id)
                elif args.operation == "reset":
                    result = store.reset()
                else:
                    from .backend import WeSpeaker

                    if not all((args.source_model, args.model, args.specialization)):
                        raise SpeakerIdentityError(
                            "inference requires --source-model, --model and --specialization"
                        )
                    backend = WeSpeaker(
                        args.source_model,
                        args.model,
                        args.specialization,
                        provider=args.provider,
                    )
                    if backend.binding != policy.embedding_binding:
                        raise SpeakerIdentityError(
                            "calibrated policy differs from runtime model/frontend"
                        )
                    embedding = backend.embed(args.wav)
                    if args.operation == "enroll":
                        result = store.enroll(
                            args.speaker_id,
                            args.label,
                            embedding.vector,
                            audio_sha256=embedding.audio_sha256,
                            embedding_binding=embedding.embedding_binding,
                        )
                    else:
                        result = asdict(
                            store.identify(
                                embedding.vector,
                                embedding_binding=embedding.embedding_binding,
                            )
                        )
                    result["runtime"] = backend.runtime
                    result["audio_seconds"] = embedding.seconds
                    result["embedding_windows"] = embedding.windows
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except (
        OSError,
        sqlite3.Error,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
        wave.Error,
        EOFError,
    ) as error:
        print(json.dumps({"status": "failed", "error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
