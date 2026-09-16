import copy
import json
from pathlib import Path
import zipfile

import pytest

from scripts.stage_native_single_prepare import candidate, first_use_overwrite, prompt_bulk_copy, graph_attribution
from scripts.audit_native_single_prepare import performance

ROOT=Path(__file__).resolve().parents[1]


def test_candidate_changes_only_the_preparation_text_not_run_or_control():
    with zipfile.ZipFile(ROOT/'deliverables/native-transpose-free-session-windows-20260913-r1/transfer/source.zip') as z:
        original=z.read('runtime/native_pocket/resident.cpp')
    updated=candidate(original)
    prefix,suffix=original.split(b'        h.base->prepare(build_preparation_request(r));')
    assert updated.startswith(prefix) and updated.endswith(suffix)
    middle=updated[len(prefix):-len(suffix)]
    assert b'preparation.text.reset();' in middle
    assert b'auto preparation = build_preparation_request(r);' in middle
    assert b'h.base->prepare(preparation);' in middle
    assert b'h.stream->start_stream(r);' in suffix
    assert b'nv_cancel' in suffix and b'nv_reset' in suffix


@pytest.mark.parametrize('raw',[b'changed', b'        h.base->prepare(build_preparation_request(r));'*2])
def test_changed_or_ambiguous_parent_is_refused(raw):
    with pytest.raises(ValueError): candidate(raw)


def test_first_use_elides_only_constructor_values_fully_overwritten_before_compute():
    original=(ROOT/'artifacts/native-pocket-source-20260910-r2/audio.cpp-3174e6b26f11a0e39b4f150961dce98f43ba860d/src/models/pocket_tts/flow_lm.cpp').read_bytes()
    changed=first_use_overwrite(original)
    assert original.count(b'core::write_tensor_')-changed.count(b'core::write_tensor_')==7
    assert original.split(b'    void apply_prompt_from_state(')[1]==changed.split(b'    void apply_prompt_from_state(')[1]
    assert original.split(b'    FlowLMStepResult run_in_place(')[1]==changed.split(b'    FlowLMStepResult run_in_place(')[1]
    assert b'core::write_tensor_f32(start_time_, start_time_buffer_);' in changed
    assert b'core::write_tensor_f32(end_time_, end_time_buffer_);' in changed
    with pytest.raises(ValueError): first_use_overwrite(original+b'changed')


def test_bulk_copy_changes_only_prompt_transfer_views():
    original=(ROOT/'artifacts/native-pocket-source-20260910-r2/audio.cpp-3174e6b26f11a0e39b4f150961dce98f43ba860d/src/models/pocket_tts/flow_lm.cpp').read_bytes()
    changed=prompt_bulk_copy(original)
    begin=original.index(b'        if (prompt_steps_ > 0) {\n            prompt_step_key_sources_.assign(')
    end=original.index(b'\n    }\n\n    void ensure_step_graph_allocated()',begin)
    assert changed.startswith(original[:begin]) and changed.endswith(original[end:])
    assert original.count(b'core::write_tensor_')==changed.count(b'core::write_tensor_')
    assert changed.count(b'assign(1, {});')==4
    with pytest.raises(ValueError): prompt_bulk_copy(original+b'changed')


def test_graph_attribution_is_insertion_only_and_names_measured_phases():
    import re
    original=(ROOT/'artifacts/native-pocket-source-20260910-r2/audio.cpp-3174e6b26f11a0e39b4f150961dce98f43ba860d/src/models/pocket_tts/flow_lm.cpp').read_bytes()
    changed=graph_attribution(original)
    assert re.sub(rb'\n// NV_DIAGNOSTIC_BEGIN\n.*?\n// NV_DIAGNOSTIC_END\n',b'',changed,flags=re.S)==original
    for i in range(8): assert f'nv_mark({i});'.encode() in changed
    assert b'NV_FLOW_CONSTRUCTOR' in changed and b'NV_FLOW_PROMPT' in changed
    assert b'NV_FLOW_STEP_ALLOCATION' in changed
    with pytest.raises(ValueError): graph_attribution(original+b'changed')


@pytest.mark.parametrize('steps,prefix,width',[(1,0,8),(2,7,16),(64,31,1024),(256,128,1024)])
def test_bulk_copy_byte_coverage_preserves_prefix_and_padding(steps,prefix,width):
    import array
    # Every element different; count includes padded prompt slots, exactly as
    # the existing graph does. Prefix and generation suffix remain sentinels.
    source=array.array('f',range(steps*width)).tobytes()
    token_bytes=4*width
    before=bytearray(b'\xcd'*((prefix+steps+3)*token_bytes))
    after=before.copy()
    for token in range(steps):
        src=token*token_bytes; dst=(prefix+token)*token_bytes
        before[dst:dst+token_bytes]=source[src:src+token_bytes]
    after[prefix*token_bytes:(prefix+steps)*token_bytes]=source
    assert before==after


def fixture():
    with zipfile.ZipFile(ROOT/'deliverables/native-tts-threads-windows-20260913-r2/windows-evidence.zip') as z:
        runs=json.loads(z.read('run/result.json'))['runs']
    for arm,r in zip(('baseline','candidate','candidate','baseline'),runs):
        r['arm']=arm
        r['rows'][0]['seconds']=3.
        for row in r['rows']:
            if row['type']=='synthesis':
                row.update(prepare_seconds=.1,first_pcm_seconds=.4 if arm=='baseline' else .3,seconds=.5,samples=24000)
    limits={'first_initial_ratio_max':.9,'first_initial_saved_seconds_min':.02,
            'every_position_first_pcm_ratio_max':1.05,'total_compute_ratio_max':1.05,
            'load_ratio_max':1.05,'every_candidate_rtf_max':1}
    return runs,limits


@pytest.mark.parametrize('damage',['load','first','later','compute','real_time'])
def test_frozen_gate_rejects_each_regression(damage):
    runs,limits=fixture()
    assert performance(runs,limits)['performance_gate_passed']
    if damage=='load': runs[1]['rows'][0]['seconds']=6
    elif damage=='first': runs[1]['rows'][1]['first_pcm_seconds']=.5
    elif damage=='later': runs[1]['rows'][2]['first_pcm_seconds']=.7
    elif damage=='compute': runs[1]['rows'][2]['seconds']=1
    else:
        for r in runs:
            for row in r['rows']:
                if row['type']=='synthesis': row['seconds']=1.1
    assert not performance(runs,limits)['performance_gate_passed']
