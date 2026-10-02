"""Export the six neural boundaries consumed by the multitalker reference.

Development tooling only: graphs and numerical parity are not a complete voice
plugin. Streaming state, enrollment, endpointing and SDK custody stay outside the
neural graphs. New output directories preserve failed exports.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time

if __package__:
    from .run_speaker_aware_reference import digest, NEMO_REV, MODEL_SHA256, MODEL_REVS, write_json
else:
    from run_speaker_aware_reference import digest, NEMO_REV, MODEL_SHA256, MODEL_REVS, write_json


def compare(actual, expected, label):
    import numpy as np
    if len(actual) != len(expected):
        raise AssertionError(label+': output census differs')
    differences = []
    for index, (got, wanted) in enumerate(zip(actual, expected)):
        if got.shape != wanted.shape or got.dtype != wanted.dtype:
            raise AssertionError(label+': shape/dtype differs')
        if not np.isfinite(got).all() or not np.isfinite(wanted).all():
            raise AssertionError(label+': nonfinite output')
        if np.issubdtype(wanted.dtype, np.integer):
            np.testing.assert_array_equal(got, wanted, err_msg=f'{label}/output-{index}')
        else:
            np.testing.assert_allclose(got, wanted, rtol=1e-3, atol=2e-4,
                                       equal_nan=False, err_msg=f'{label}/output-{index}')
        differences.append(float(np.max(np.abs(got-wanted), initial=0)))
    return differences


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--nemo', type=Path, required=True)
    p.add_argument('--models', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--ort-optimization', choices=['disabled', 'basic', 'extended', 'all'],
                   default='disabled', help='Record and qualify the chosen graph transformation level')
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    result = dict(passed=False, scope='six neural graph exports, synthetic numerical parity only',
                  installed=False, speaker_identification_qualified=False, graphs=[],
                  ort_optimization=a.ort_optimization)
    try:
        if subprocess.check_output(['git','-C',str(a.nemo),'rev-parse','HEAD'],text=True).strip() != NEMO_REV:
            raise ValueError('upstream revision differs')
        if subprocess.check_output(['git','-C',str(a.nemo),'status','--porcelain'],text=True):
            raise ValueError('upstream is dirty')
        pins = json.loads(a.models.read_text())['models']
        if len(pins) != 2 or {r['repo'] for r in pins} != set(MODEL_SHA256):
            raise ValueError('model census differs')
        for row in pins:
            if (digest(row['path']) != MODEL_SHA256[row['repo']] or
                    row['revision'] != MODEL_REVS[row['repo']] or
                    row['sha256'] != MODEL_SHA256[row['repo']] or
                    Path(row['path']).stat().st_size != row['bytes']):
                raise ValueError('checkpoint binding differs')
        result.update(upstream_revision=NEMO_REV, exporter_sha256=digest(__file__),
                      models_manifest_sha256=digest(a.models), models=pins)
        import numpy as np
        import torch
        import onnxruntime as ort
        import nemo
        from nemo.core.classes import typecheck
        from nemo.collections.asr.models.multitalker_asr_models import EncDecMultiTalkerRNNTBPEModel
        from nemo.collections.asr.models.sortformer_diar_models import SortformerEncLabelModel
        if not Path(nemo.__file__).resolve().is_relative_to(a.nemo.resolve()):
            raise ValueError('imported upstream differs from verified checkout')
        torch.set_num_threads(2); torch.manual_seed(17)
        paths = {r['repo']: r['path'] for r in pins}
        asr = EncDecMultiTalkerRNNTBPEModel.restore_from(
            paths['nvidia/multitalker-parakeet-streaming-0.6b-v1'], map_location='cpu').eval()
        diar = SortformerEncLabelModel.restore_from(
            paths['nvidia/diar_streaming_sortformer_4spk-v2.1'], map_location='cpu').eval()
        if asr.spk_kernel_type != 'ff' or list(asr.spk_kernel_layers) != [0] or not asr.add_bg_spk_kernel:
            raise ValueError('unexpected speaker conditioning')
        if diar.high_resolution:
            raise ValueError('different diarization time resolution')
        if (asr.decoder.blank_idx != 1024 or asr.cfg.decoder.prednet.pred_hidden != 640 or
                asr.cfg.decoder.prednet.pred_rnn_layers != 2 or asr.cfg.encoder.d_model != 1024):
            raise ValueError('different recognizer geometry')

        class Preencode(torch.nn.Module):
            def __init__(self, owner):
                super().__init__(); self.owner = owner
            def forward(self, features, lengths):
                x, n = self.owner.encoder.pre_encode(x=features, lengths=lengths)
                return x, n.to(torch.int64)

        class ConditionedEncoder(torch.nn.Module):
            def __init__(self, owner):
                super().__init__(); self.owner = owner
            def forward(self, embeddings, lengths, channel, temporal, valid, foreground, background):
                self.owner.set_speaker_targets(foreground, background)
                # Exactly the pre-encoded boundary used by parallel streaming.
                # The owner removes raw-feature cache frames before this call
                # and trims outputs after it, including keep-all final chunks.
                return self.owner.encoder.forward_internal(embeddings, lengths,
                    cache_last_channel=channel, cache_last_time=temporal,
                    cache_last_channel_len=valid, bypass_pre_encode=True)

        class Decoder(torch.nn.Module):
            def __init__(self, owner):
                super().__init__(); self.owner = owner.decoder
            def forward(self, tokens, hidden, cell):
                pred, state = self.owner.predict(tokens, (hidden, cell), add_sos=False)
                return pred.transpose(1, 2), state[0], state[1]

        class Joiner(torch.nn.Module):
            def __init__(self, owner):
                super().__init__(); self.owner = owner.joint
            def forward(self, encoded, predicted):
                return self.owner.joint(encoded.transpose(1, 2), predicted.transpose(1, 2))

        class DiarClassifier(torch.nn.Module):
            def __init__(self, owner):
                super().__init__(); self.owner = owner
            def forward(self, embeddings, lengths):
                features, lens = self.owner.frontend_encoder(embeddings, lengths, bypass_pre_encode=True)
                return self.owner.forward_infer(features, lens)

        graph_sessions = {}
        def export(name, module, example, ins, outs, dynamic, cases):
            folder = a.out/name; folder.mkdir()
            observed=[]
            graph_result=dict(name=name,inputs=ins,outputs=outs,cases=observed,passed=False)
            result['graphs'].append(graph_result)
            with torch.no_grad(), typecheck.disable_checks():
                torch.onnx.export(module.eval(), example, str(folder/'model.onnx'),
                    input_names=ins, output_names=outs, dynamic_axes=dynamic,
                    opset_version=18, dynamo=False, external_data=True, do_constant_folding=True)
                options = ort.SessionOptions(); options.intra_op_num_threads=2; options.inter_op_num_threads=1
                options.graph_optimization_level = {
                    'disabled': ort.GraphOptimizationLevel.ORT_DISABLE_ALL,
                    'basic': ort.GraphOptimizationLevel.ORT_ENABLE_BASIC,
                    'extended': ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED,
                    'all': ort.GraphOptimizationLevel.ORT_ENABLE_ALL,
                }[a.ort_optimization]
                session = ort.InferenceSession(str(folder/'model.onnx'), sess_options=options,
                                               providers=['CPUExecutionProvider'])
                if [x.name for x in session.get_inputs()] != ins or [x.name for x in session.get_outputs()] != outs:
                    raise AssertionError(name+': graph signature changed')
                for label, values in cases:
                    graph_result['active_case']=label
                    expected=module(*values)
                    if isinstance(expected, torch.Tensor): expected=(expected,)
                    expected=[x.detach().numpy().copy() for x in expected]
                    native=session.run(None,{n:x.detach().numpy() for n,x in zip(ins,values)})
                    try:
                        errors=compare(native,expected,name+'/'+label)
                        observed.append(dict(case=label, passed=True, max_absolute_errors=errors))
                    except AssertionError as error:
                        # Keep exporting independent boundaries to expose the complete
                        # failure set. A failed case still fails the entire gate.
                        observed.append(dict(case=label, passed=False, error=str(error)))
                graph_sessions[name]=session
                graph_result.pop('active_case')
                graph_result['passed']=all(case['passed'] for case in observed)

        def feature_case(batch, frames):
            return torch.randn(batch,frames,128),torch.full((batch,),frames,dtype=torch.int64)
        with torch.no_grad(), typecheck.disable_checks():
            for name, owner in [('asr_preencode',asr),('diar_preencode',diar)]:
                ins=['features','lengths']; outs=['embeddings','encoded_lengths']
                # One resident capture is pre-encoded ONCE, before any speaker
                # expansion. Two speakers are NOT two independent captures.
                # Only the conditioned encoder/decoder have a speaker batch.
                axes={'features':{1:'feature_frames'},
                      'embeddings':{1:'encoded_frames'}}
                cases=[(label,feature_case(b,n)) for label,b,n in
                       [('first',1,105),('cached',1,121),('short',1,25)]]
                export(name,Preencode(owner),cases[1][1],ins,outs,axes,cases)
                session=graph_sessions[name]
                if session.get_inputs()[0].shape[0]!=1:
                    raise AssertionError('preencoder must own exactly one capture')
                rejected=False
                try:
                    session.run(None,{n:x.numpy() for n,x in zip(ins,feature_case(2,121))})
                except ort.capi.onnxruntime_pybind11_state.InvalidArgument:
                    rejected=True
                if not rejected:
                    raise AssertionError('multiple captures entered one preencoder')
                result['graphs'][-1]['multiple_captures_refused']=True
            def encoder_case(batch, frames, fg):
                channel,temporal,valid=asr.encoder.get_initial_cache_state(batch_size=batch,device='cpu')
                channel.zero_();temporal.zero_();valid.zero_()
                return (torch.randn(batch,frames,1024),torch.full((batch,),frames,dtype=torch.int64),
                        channel,temporal,valid,torch.full((batch,frames),float(fg)),
                        torch.full((batch,frames),float(1-fg)))
            ins=['embeddings','lengths','channel','temporal','valid','foreground','background']
            outs=['encoded','encoded_lengths','next_channel','next_temporal','next_valid']
            axes={n:{0:'batch',1:'frames'} for n in ['embeddings','foreground','background']}
            axes.update({n:{0:'batch'} for n in ['lengths','valid','encoded_lengths','next_valid']})
            axes.update({n:{1:'batch'} for n in ['channel','temporal','next_channel','next_temporal']})
            axes['encoded']={0:'batch',2:'frames'}
            cases=[('first',encoder_case(1,14,1)),('pair',encoder_case(2,14,1)),
                   ('short_final',encoder_case(1,4,1))]
            changed=list(cases[0][1]);changed[-2:]=[changed[-1],changed[-2]]
            cases.append(('different_target_same_audio',tuple(changed)))
            export('asr_encoder',ConditionedEncoder(asr),cases[0][1],ins,outs,axes,cases)
            native=graph_sessions['asr_encoder']
            rows=[native.run(None,{n:x.numpy() for n,x in zip(ins,c[1])}) for c in (cases[0],cases[-1])]
            delta=float(np.max(np.abs(rows[0][0]-rows[1][0])))
            if not np.isfinite(delta) or delta<=1e-4:raise AssertionError('speaker conditioning disappeared')
            result['same_audio_target_delta']=delta
            def decoder_case(batch, token):
                return (torch.full((batch,1),token,dtype=torch.int64),
                        torch.zeros(2,batch,640),torch.zeros(2,batch,640))
            cases=[('blank',decoder_case(1,asr.decoder.blank_idx)),('token',decoder_case(1,1)),
                   ('pair',decoder_case(2,2))]
            # Native priming uses the blank embedding in place of upstream's
            # None token. Prove the substitution instead of assuming it.
            blank_pred,blank_state=asr.decoder.predict(cases[0][1][0],cases[0][1][1:],add_sos=False)
            null_pred,null_state=asr.decoder.predict(None,None,add_sos=False,batch_size=1)
            compare([x.numpy() for x in (blank_pred,*blank_state)],
                    [x.numpy() for x in (null_pred,*null_state)],'blank_equals_start')
            result['blank_equals_start']=True
            export('asr_decoder',Decoder(asr),cases[0][1],['tokens','hidden','cell'],
                ['predicted','next_hidden','next_cell'],
                {'tokens':{0:'batch'},'hidden':{1:'batch'},'cell':{1:'batch'},
                 'predicted':{0:'batch'},'next_hidden':{1:'batch'},'next_cell':{1:'batch'}},cases)
            cases=[(str(b),(torch.randn(b,1024,1),torch.randn(b,640,1))) for b in (1,2)]
            export('asr_joiner',Joiner(asr),cases[0][1],['encoded','predicted'],['logits'],
                   {n:{0:'batch'} for n in ['encoded','predicted','logits']},cases)
            cases=[(str(n),(torch.randn(1,n,512),torch.tensor([n],dtype=torch.int64))) for n in (14,28,64)]
            export('diar_classifier',DiarClassifier(diar),cases[1][1],['embeddings','lengths'],['probabilities'],
                {'embeddings':{0:'batch',1:'frames'},'lengths':{0:'batch'},
                 'probabilities':{0:'batch',1:'frames'}},cases)
        from omegaconf import OmegaConf
        configuration=dict(sample_rate=16000,feature_stride_samples=160,encoder_stride_features=8,
            encoder=OmegaConf.to_container(asr.cfg.encoder,resolve=True),
            decoder=OmegaConf.to_container(asr.cfg.decoder,resolve=True),
            blank_id=asr.decoder.blank_idx,
            asr_streaming=vars(asr.encoder.streaming_cfg),
            diar_streaming=OmegaConf.to_container(diar.cfg.sortformer_modules,resolve=True),
            native_runtime_integrated=False)
        write_json(a.out/'configuration.json',configuration)
        write_json(a.out/'tokens.json',[asr.tokenizer.ids_to_tokens([i])[0] for i in range(asr.decoder.blank_idx)])
        if any(digest(r['path'])!=MODEL_SHA256[r['repo']] for r in pins):
            raise ValueError('checkpoint changed during export')
        result['artifacts']={f.relative_to(a.out).as_posix():dict(sha256=digest(f),bytes=f.stat().st_size)
                             for f in sorted(a.out.rglob('*')) if f.is_file()}
        result['versions']=dict(torch=torch.__version__,onnxruntime=ort.__version__)
        result['passed']=all(graph['passed'] for graph in result['graphs'])
    except BaseException as e:
        result['error']=repr(e)
        raise
    finally:
        result['elapsed_seconds']=time.monotonic()-started
        write_json(a.out/'result.json',result)
        print(json.dumps({k:result.get(k) for k in ['passed','error','elapsed_seconds']}),flush=True)
    if not result['passed']:
        raise SystemExit(1)


if __name__=='__main__':main()
