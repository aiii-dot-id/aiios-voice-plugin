"""Bind the native frontend coefficients to the pinned recognizer configuration."""
import argparse
import io
import json
from pathlib import Path
import tarfile

from .run_speaker_aware_reference import digest, MODEL_SHA256, write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    expected=MODEL_SHA256['nvidia/multitalker-parakeet-streaming-0.6b-v1']
    if digest(args.model)!=expected:raise ValueError('recognizer checkpoint differs')
    import numpy as np
    import yaml
    import torch
    from nemo.collections.asr.modules import AudioToMelSpectrogramPreprocessor
    from nemo.core.classes import typecheck
    with tarfile.open(args.model) as archive:
        member=archive.getmember('model_config.yaml')
        if not member.isfile() or member.size>1048576:raise ValueError('model configuration extent')
        config=yaml.safe_load(archive.extractfile(member))['preprocessor']
    required=dict(sample_rate=16000,normalize='NA',window_size=.025,window_stride=.01,
                  window='hann',features=128,n_fft=512,frame_splicing=1,pad_to=0,log=True)
    if any(config.get(k)!=v for k,v in required.items()):raise ValueError('frontend geometry differs')
    config=dict(config);config.pop('_target_');config['dither']=0.
    model=AudioToMelSpectrogramPreprocessor(**config).eval()
    filters=model.featurizer.fb.detach().cpu().numpy().astype('<f4')
    if filters.shape!=(1,128,257):raise ValueError('filter geometry differs')
    (args.out/'mel.f32').write_bytes(filters.tobytes())
    rng=np.random.default_rng(17)
    pcm=(rng.standard_normal(48000)*.1).astype('<f4')
    with torch.no_grad(),typecheck.disable_checks():
        features,lengths=model(input_signal=torch.from_numpy(pcm[None]),length=torch.tensor([len(pcm)]))
    n=int(lengths[0]);features=features[0,:,:n].transpose(0,1).numpy().astype('<f4')
    (args.out/'pcm.f32').write_bytes(pcm.tobytes())
    (args.out/'features.f32').write_bytes(features.tobytes())
    write_json(args.out/'result.json',dict(model_sha256=expected,configuration=config,
        samples=len(pcm),frames=n,files={p.name:digest(p) for p in args.out.iterdir() if p.is_file()}))
    print(json.dumps(dict(frames=n,model_sha256=expected)))


if __name__=='__main__':main()
