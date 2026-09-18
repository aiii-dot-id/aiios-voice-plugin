import copy
import json

import pytest

from scripts.assemble_guided_beta_candidate import apply_distribution_disposition, sha


def fixture(tmp_path):
    index={'distribution_review_complete':False,'open_items':['export history','source build']}
    models=[{'path':p,'sha256':p+'-bytes'} for p in ('vad/model.onnx','endpoint/model.onnx')]
    profiles={'windows':{'files':{'bin/asmjit.dll':{'sha256':'asmjit-bytes'}}}}
    components=[{'component':m['path'],'sha256':m['sha256'],'notice_sha256':m['path']+'-notice'} for m in models]
    components.append({'component':'windows/bin/asmjit.dll','sha256':'asmjit-bytes','notice_sha256':'asmjit-notice'})
    prior=dict(passed=True,engineering_distribution_review='named_items_resolved',
               resolved_items=index['open_items'],reproducibility_limits_retained=index['open_items'],
               components=components,signed_package_sha256='historical-package',reasoning=['named disposition'],scope='only named components')
    source=tmp_path/'prior.json';source.write_text(json.dumps(prior))
    return index,{'models':models},profiles,[{'sha256':c['notice_sha256']} for c in components],source


def test_bound_prior_review_resolves_only_distribution(tmp_path):
    index,cfg,profiles,notices,source=fixture(tmp_path)
    apply_distribution_disposition(index,cfg,profiles,notices,source,sha(source))
    assert index['distribution_review_complete'] is True and index['open_items']==[]
    assert index['reproducibility_limits']==['export history','source build']
    assert index['distribution_disposition']['previous_signature_or_installation_inherited'] is False
    assert 'signed' not in index and 'installed' not in index and 'beta_release_ready' not in index


@pytest.mark.parametrize('damage',['receipt','model','dll','notice','new-question','component-census','limits'])
def test_changed_inputs_cannot_inherit_disposition(tmp_path,damage):
    index,cfg,profiles,notices,source=fixture(tmp_path)
    digest=sha(source)
    if damage=='receipt':digest='wrong'
    if damage=='model':cfg['models'][0]['sha256']='different'
    if damage=='dll':profiles['windows']['files']['bin/asmjit.dll']['sha256']='different'
    if damage=='notice':notices.pop()
    if damage=='new-question':index['open_items'].append('new unresolved item')
    if damage in ('component-census','limits'):
        prior=json.loads(source.read_text())
        if damage=='component-census':prior['components'].pop()
        else:prior['reproducibility_limits_retained']=[]
        source.write_text(json.dumps(prior));digest=sha(source)
    before=copy.deepcopy(index)
    with pytest.raises(ValueError):apply_distribution_disposition(index,cfg,profiles,notices,source,digest)
    assert index==before
