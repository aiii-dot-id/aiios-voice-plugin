"""Export the pinned speaker-conditioned cached encoder and check ORT parity.

Development-only export: this is not a native session, transcription, speaker
matching, accelerator qualification, or release-admission result. Inputs have a
fixed streaming-chunk shape; speaker masks remain explicit runtime inputs.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time

if __package__:
    from .run_speaker_aware_reference import digest, NEMO_REV, MODEL_SHA256, write_json
else:
    from run_speaker_aware_reference import digest, NEMO_REV, MODEL_SHA256, write_json


INPUT_NAMES = ['audio_signal', 'length', 'cache_last_channel', 'cache_last_time',
               'cache_last_channel_len', 'speaker_targets', 'background_targets']
OUTPUT_NAMES = ['encoded', 'encoded_len', 'cache_last_channel_next',
                'cache_last_time_next', 'cache_last_channel_next_len']


def require_inputs(actual_names):
    if list(actual_names) != INPUT_NAMES:
        raise AssertionError('encoder input disappeared, changed or reordered')


def compare_outputs(native, expected, case):
    import numpy as np
    if len(native) != len(OUTPUT_NAMES) or len(expected) != len(OUTPUT_NAMES):
        raise AssertionError('encoder output count differs')
    errors = []
    for name, got, wanted in zip(OUTPUT_NAMES, native, expected):
        if got.shape != wanted.shape or got.dtype != wanted.dtype:
            raise AssertionError(case+'/'+name+': shape or dtype differs')
        if not np.isfinite(got).all() or not np.isfinite(wanted).all():
            raise AssertionError(case+'/'+name+': nonfinite output')
        if np.issubdtype(wanted.dtype, np.integer):
            np.testing.assert_array_equal(got, wanted, err_msg=case+'/'+name)
        else:
            np.testing.assert_allclose(got, wanted, rtol=1e-3, atol=2e-4,
                                       equal_nan=False, err_msg=case+'/'+name)
        errors.append(dict(output=name, shape=list(got.shape),
                           max_absolute_error=float(np.max(np.abs(got-wanted), initial=0))))
    return errors


def require_target_effect(foreground, background):
    import numpy as np
    if foreground.shape != background.shape or not foreground.size:
        raise AssertionError('different target outputs cannot be compared')
    delta = float(np.max(np.abs(foreground-background)))
    if not np.isfinite(delta) or delta <= 1e-4:
        raise AssertionError('same audio ignores different target speaker')
    return delta


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--nemo', type=Path, required=True)
    p.add_argument('--model', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    result = dict(passed=False, scope='cached encoder export numerical parity only',
                  installed=False, transcript_qualification=False, enrolled_identity_qualification=False)
    try:
        rev = subprocess.check_output(['git', '-C', str(a.nemo), 'rev-parse', 'HEAD'], text=True).strip()
        dirty = subprocess.check_output(['git', '-C', str(a.nemo), 'status', '--porcelain'], text=True)
        if rev != NEMO_REV or dirty:
            raise ValueError('exact clean upstream required')
        bound = digest(a.model)
        if bound != MODEL_SHA256['nvidia/multitalker-parakeet-streaming-0.6b-v1']:
            raise ValueError('checkpoint differs')
        import torch
        import numpy as np
        import onnxruntime as ort
        import nemo
        from nemo.collections.asr.models.multitalker_asr_models import EncDecMultiTalkerRNNTBPEModel
        from nemo.core.classes import typecheck

        if not Path(nemo.__file__).resolve().is_relative_to(a.nemo.resolve()):
            raise ValueError('imported NeMo differs from the verified checkout')
        torch.set_num_threads(2)
        torch.manual_seed(17)
        model = EncDecMultiTalkerRNNTBPEModel.restore_from(str(a.model), map_location='cpu').eval()
        model.preprocessor.featurizer.dither = 0
        model.encoder.export_cache_support = True
        result.update(nemo_revision=rev, checkpoint_sha256=bound, probe_sha256=digest(__file__),
                      versions=dict(torch=torch.__version__, onnxruntime=ort.__version__),
                      kernel_type=model.spk_kernel_type, kernel_layers=list(model.spk_kernel_layers),
                      background_kernel=model.add_bg_spk_kernel)
        if model.spk_kernel_type != 'ff' or list(model.spk_kernel_layers) != [0] or not model.add_bg_spk_kernel:
            raise ValueError('unexpected speaker-kernel structure')

        class Encoder(torch.nn.Module):
            def __init__(self, owner):
                super().__init__()
                self.owner = owner

            def forward(self, audio_signal, length, cache_last_channel, cache_last_time,
                        cache_last_channel_len, speaker_targets, background_targets):
                self.owner.set_speaker_targets(speaker_targets, background_targets)
                return self.owner.encoder.forward_for_export(audio_signal, length,
                    cache_last_channel, cache_last_time, cache_last_channel_len)

        encoder = Encoder(model).eval()
        signal, length, channel, temporal, valid = model.encoder.input_example(max_batch=1)
        length[:] = signal.shape[-1]
        channel.zero_(); temporal.zero_(); valid.zero_()
        frames = (signal.shape[-1] + 7) // 8
        fg = torch.ones((1, frames), dtype=torch.float32)
        bg = torch.zeros_like(fg)
        inputs = (signal, length, channel, temporal, valid, fg, bg)
        names, outputs = INPUT_NAMES, OUTPUT_NAMES
        result['inputs'] = {n: dict(shape=list(t.shape), dtype=str(t.dtype)) for n, t in zip(names, inputs)}
        result['streaming_config'] = str(model.encoder.streaming_cfg)
        write_json(a.out/'start.json', result)
        with torch.no_grad(), typecheck.disable_checks():
            torch.onnx.export(encoder, inputs, str(a.out/'encoder.onnx'),
                input_names=names, output_names=outputs, opset_version=18,
                dynamo=False, external_data=True, do_constant_folding=True)
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            options.enable_mem_pattern = False
            session = ort.InferenceSession(str(a.out/'encoder.onnx'), sess_options=options,
                                           providers=['CPUExecutionProvider'])
            require_inputs(x.name for x in session.get_inputs())
            result['cases'] = []
            previous = {}
            for case, foreground, background, owner, short in [
                    ('foreground', fg, bg, None, False),
                    ('background', bg, fg, None, False),
                    ('overlap', fg, fg, None, False),
                    ('silence', bg, bg, None, False),
                    ('foreground_next', fg, bg, 'foreground', False),
                    ('background_next', bg, fg, 'background', False),
                    ('short_final', fg, bg, 'foreground_next', True),
                    ('fresh_after_other_track', fg, bg, None, False)]:
                values = list(inputs)
                values[-2:] = [foreground, background]
                expected_values = list(values)
                if owner is not None:
                    # Each path consumes its own previous state: never feed the
                    # reference with native state and thereby hide drift.
                    native_before, reference_before = previous[owner]
                    values[2:5] = [torch.from_numpy(x.copy()) for x in native_before[2:5]]
                    expected_values[2:5] = [torch.from_numpy(x.copy()) for x in reference_before[2:5]]
                if short:
                    values[1] = torch.maximum(length - 8, torch.ones_like(length))
                    expected_values[1] = values[1]
                expected = [x.detach().numpy().copy() for x in encoder(*expected_values)]
                native = session.run(None, {n: t.detach().numpy() for n, t in zip(names, values)})
                errors = compare_outputs(native, expected, case)
                previous[case] = native, expected
                result['cases'].append(dict(case=case, cache_owner=owner, outputs=errors))
            compare_outputs(previous['fresh_after_other_track'][0], previous['foreground'][0], 'state_reset')
            result['same_audio_target_change_max_delta'] = require_target_effect(
                previous['foreground'][0][0], previous['background'][0][0])
        if digest(a.model) != bound:
            raise ValueError('checkpoint changed during export')
        result['artifacts'] = {f.name:dict(sha256=digest(f), bytes=f.stat().st_size)
                               for f in sorted(a.out.iterdir()) if f.is_file() and f.name != 'start.json'}
        result['passed'] = True
    except BaseException as error:
        result['error'] = repr(error)
        raise
    finally:
        result['elapsed_seconds'] = time.monotonic()-start
        write_json(a.out/'result.json', result)
        print(json.dumps({k:result.get(k) for k in ('passed','error','elapsed_seconds')}), flush=True)


if __name__ == '__main__':
    main()
