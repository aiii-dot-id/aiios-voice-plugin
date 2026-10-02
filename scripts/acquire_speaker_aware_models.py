"""Download only two pinned, public reference checkpoints; verify LFS hashes."""
import argparse
import hashlib
import json
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download

MODELS = (
    ("nvidia/diar_streaming_sortformer_4spk-v2.1",
     "fafaab5faa1617a0ca52d38dd3dc4bd636800d3d",
     "diar_streaming_sortformer_4spk-v2.1.nemo"),
    ("nvidia/multitalker-parakeet-streaming-0.6b-v1",
     "8749fc71fd6e2d88ef230159bbf2aea69b524ee1",
     "multitalker-parakeet-streaming-0.6b-v1.nemo"),
)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    result = {"schema": 1, "verified": False, "models": []}
    for repo, revision, name in MODELS:
        info = HfApi(token=False).model_info(repo, revision=revision, files_metadata=True)
        if info.sha != revision:
            raise ValueError("model revision changed")
        entry = next(x for x in info.siblings if x.rfilename == name)
        if not entry.lfs or not entry.lfs.sha256:
            raise ValueError("missing upstream checkpoint digest")
        path = Path(hf_hub_download(repo, name, revision=revision, token=False,
                                   local_dir=a.output / repo.split("/")[-1]))
        with path.open("rb") as f:
            digest = hashlib.file_digest(f, "sha256").hexdigest()
        if digest != entry.lfs.sha256 or path.stat().st_size != entry.size:
            raise ValueError("checkpoint hash or size differs")
        result["models"].append(dict(repo=repo, revision=revision, path=str(path.resolve()),
                                     sha256=digest, bytes=path.stat().st_size))
        print(json.dumps({"verified": repo, "sha256": digest}), flush=True)
    result["verified"] = True
    with (a.output / "manifest.json").open("x") as f:
        json.dump(result, f, indent=2, allow_nan=False)


if __name__ == "__main__":
    main()
