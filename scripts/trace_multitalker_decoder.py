"""Capture actual recorded-speech decoder inputs from the pinned reference.

No neural weights, features, inference options, speaker masks or reference
transcripts are changed. This is a development trace, not a plugin runtime.
The native decoder consumes each encoder input before reading expected tokens.
"""
import argparse
import json
from pathlib import Path
import runpy
import struct
import subprocess
import sys
import time

from .run_speaker_aware_reference import digest, NEMO_REV, MODEL_SHA256, write_json


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference',type=Path,required=True)
    p.add_argument('--nemo',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    a.out=a.out.resolve(); a.out.mkdir(parents=True,exist_ok=False)
    result=dict(passed=False,installed=False,scope='recorded reference decoder trace',records=0,epochs=0)
    started=time.monotonic()
    try:
        reference=json.loads(a.reference.read_text())
        if reference['status']!='passed' or reference['nemo_revision']!=NEMO_REV:
            raise ValueError('a passing pinned reference is required')
        if subprocess.check_output(['git','-C',str(a.nemo),'rev-parse','HEAD'],text=True).strip()!=NEMO_REV:
            raise ValueError('upstream revision differs')
        if subprocess.check_output(['git','-C',str(a.nemo),'status','--porcelain'],text=True):
            raise ValueError('upstream is dirty')
        for row in reference['models']:
            if digest(row['path'])!=MODEL_SHA256[row['repo']]:raise ValueError('checkpoint binding')
        manifest=a.reference.parent/'inference.jsonl'
        if digest(manifest)!=reference['inference_sha256']:raise ValueError('inference manifest changed')
        import numpy as np
        import nemo
        if not Path(nemo.__file__).resolve().is_relative_to(a.nemo.resolve()):
            raise ValueError('upstream import path differs')
        from nemo.collections.asr.parts.utils.multispk_transcribe_utils import MultiTalkerInstanceManager, SpeakerTaggedASR
        from nemo.collections.asr.parts.mixins.multitalker_asr_mixins import SpeakerKernelMixin
        from nemo.collections.asr.parts.mixins.mixins import ASRModuleMixin
        from nemo.collections.asr.parts.submodules.rnnt_decoding import AbstractRNNTDecoding
        original_reset=MultiTalkerInstanceManager.reset
        original_active=MultiTalkerInstanceManager.get_active_speakers_info
        original_decode=AbstractRNNTDecoding.rnnt_decoder_predictions_tensor
        original_targets=SpeakerKernelMixin.set_speaker_targets
        # conformer_stream_step is inherited from ASRModuleMixin in this pin.
        original_step=ASRModuleMixin.conformer_stream_step
        original_capture=SpeakerTaggedASR.perform_parallel_streaming_stt_spk
        epoch=0; owners=[]; clocks={}; geometry=set(); active_inputs=None; targets=None; final_chunk=False
        def reset(owner,*args,**kwargs):
            nonlocal epoch,clocks
            value=original_reset(owner,*args,**kwargs)
            epoch+=1;clocks={}
            return value
        def active(owner,active_speakers,*args,**kwargs):
            nonlocal owners
            if len(active_speakers)!=1:raise ValueError('trace supports one conversation per batch')
            owners=list(active_speakers[0])
            return original_active(owner,active_speakers,*args,**kwargs)
        def set_targets(owner,spk_targets=None,bg_spk_targets=None):
            nonlocal targets
            targets=(spk_targets,bg_spk_targets)
            return original_targets(owner,spk_targets,bg_spk_targets)
        def step(owner,*args,**kwargs):
            nonlocal active_inputs,final_chunk
            if args or not kwargs.get('bypass_pre_encode'):
                raise ValueError('reference did not use the pre-encoded speaker boundary')
            active_inputs=(kwargs['processed_signal'],kwargs['processed_signal_length'])
            final_chunk=kwargs['keep_all_outputs']
            return original_step(owner,**kwargs)
        trace_path=a.out/'decoder.trace'
        conditioned_path=a.out/'conditioned.trace'
        capture_path=a.out/'capture.trace'
        with trace_path.open('xb') as trace, conditioned_path.open('xb') as conditioned, capture_path.open('xb') as capture:
            trace.write(b'AIIMTR01')
            conditioned.write(b'AIIMTR02')
            capture.write(b'AIIMTC01')
            def capture_step(owner,step_num,chunk_audio,chunk_lengths,is_buffer_empty,
                             drop_extra_pre_encoded,diar_chunk_audio=None,diar_chunk_lengths=None):
                if chunk_audio.shape[0]!=1 or chunk_audio.shape[1]!=128:
                    raise ValueError('capture trace requires one 128-bin microphone')
                value=original_capture(owner,step_num=step_num,chunk_audio=chunk_audio,
                    chunk_lengths=chunk_lengths,is_buffer_empty=is_buffer_empty,
                    drop_extra_pre_encoded=drop_extra_pre_encoded,
                    diar_chunk_audio=diar_chunk_audio,diar_chunk_lengths=diar_chunk_lengths)
                features=chunk_audio[0].transpose(0,1).detach().cpu().numpy().astype('<f4')
                frames=len(features);valid=int(chunk_lengths[0])
                if not 0<valid<=frames<=1024 or not np.isfinite(features).all():
                    raise ValueError('capture features invalid')
                capture.write(struct.pack('<IIIII',epoch,frames,valid,drop_extra_pre_encoded,int(is_buffer_empty)))
                capture.write(features.tobytes())
                state=owner.instance_manager.batch_asr_states[0]
                for track in range(4):
                    hypothesis=state.previous_hypothesis[track] if track<len(state.previous_hypothesis) else None
                    tokens=[] if hypothesis is None else [int(x) for x in hypothesis.y_sequence]
                    capture.write(struct.pack('<I',len(tokens)))
                    capture.write(struct.pack('<'+'I'*len(tokens),*tokens))
                return value
            def decode(owner,encoder_output,encoded_lengths,return_hypotheses=False,partial_hypotheses=None):
                value=original_decode(owner,encoder_output,encoded_lengths,return_hypotheses,partial_hypotheses)
                if not return_hypotheses or len(value)!=len(owners):
                    raise ValueError('decoder ownership census differs')
                maximum=getattr(owner.decoding,'max_symbols',None)
                if maximum!=10:raise ValueError('decoder symbol bound differs')
                for i,track in enumerate(owners):
                    n=int(encoded_lengths[i]);first=clocks.get(track,0)
                    if n<=0 or n>128 or track<0 or track>=4:raise ValueError('decoder extent differs')
                    encoded=encoder_output[i,:,:n].transpose(0,1).detach().cpu().numpy().astype('<f4')
                    if encoded.shape!=(n,1024) or not np.isfinite(encoded).all():raise ValueError('decoder input differs')
                    tokens=[int(x) for x in value[i].y_sequence]
                    if any(x<0 or x>=1024 for x in tokens):raise ValueError('reference token outside vocabulary')
                    trace.write(struct.pack('<IIII',epoch,track,first,n))
                    trace.write(encoded.tobytes())
                    trace.write(struct.pack('<I',len(tokens)))
                    trace.write(struct.pack('<'+'I'*len(tokens),*tokens))
                    features=active_inputs[0][i].detach().cpu().numpy().astype('<f4')
                    valid=int(active_inputs[1][i]); frames=len(features)
                    if features.shape!=(frames,1024) or valid<=0 or valid>frames or frames>128:
                        raise ValueError('conditioned encoder input geometry')
                    conditioned.write(struct.pack('<IIIIII',epoch,track,first,frames,valid,int(final_chunk)))
                    conditioned.write(features.tobytes())
                    for mask,default in zip(targets,(1.,0.)):
                        values=mask[i].detach().cpu().numpy().astype('<f4')
                        # This is the pinned speaker-kernel length alignment,
                        # made explicit at the native graph boundary.
                        if len(values)<frames:values=np.pad(values,(frames-len(values),0),constant_values=default)
                        else:values=values[-frames:]
                        if not np.isin(values,[0.,1.]).all():raise ValueError('nonbinary targets')
                        conditioned.write(values.tobytes())
                    conditioned.write(struct.pack('<I',len(tokens)))
                    conditioned.write(struct.pack('<'+'I'*len(tokens),*tokens))
                    clocks[track]=first+n; result['records']+=1
                    geometry.add((epoch,track))
                return value
            MultiTalkerInstanceManager.reset=reset
            MultiTalkerInstanceManager.get_active_speakers_info=active
            AbstractRNNTDecoding.rnnt_decoder_predictions_tensor=decode
            SpeakerKernelMixin.set_speaker_targets=set_targets
            ASRModuleMixin.conformer_stream_step=step
            SpeakerTaggedASR.perform_parallel_streaming_stt_spk=capture_step
            upstream=a.nemo/'examples/asr/asr_cache_aware_streaming/speech_to_text_multitalker_streaming_infer.py'
            if digest(upstream)!=reference['upstream_script_sha256']:raise ValueError('upstream script binding')
            raw=a.out/'upstream.seglst.json'
            # Copy only the explicit, previously qualified arguments, replacing
            # output locations. Never copy an inherited credential environment.
            command=reference['command'][2:]
            command=[('output_path='+str(raw)) if x.startswith('output_path=') else
                     ('hydra.run.dir='+str(a.out/'hydra')) if x.startswith('hydra.run.dir=') else x
                     for x in command]
            sys.argv=[str(upstream),*command]
            runpy.run_path(str(upstream),run_name='__main__')
        if not result['records']:raise ValueError('empty decoder trace')
        if digest(raw)!=reference['raw_output_sha256']:raise ValueError('tracing changed reference output')
        result.update(passed=True,epochs=epoch,tracks=sorted(geometry),trace_sha256=digest(trace_path),
                      conditioned_trace_sha256=digest(conditioned_path),
                      capture_trace_sha256=digest(capture_path),
                      reference_result_sha256=digest(a.reference),source_sha256=digest(__file__),
                      upstream_revision=NEMO_REV,raw_output_sha256=digest(raw),models=reference['models'])
    except BaseException as error:
        result['error']=repr(error)
        raise
    finally:
        result['elapsed_seconds']=time.monotonic()-started
        write_json(a.out/'result.json',result)
        print(json.dumps(result),flush=True)


if __name__=='__main__':main()
