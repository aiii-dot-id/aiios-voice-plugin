import copy
import hashlib
import json

import pytest

from scripts.assemble_guided_beta_candidate import hearing_replacement


def fixture(tmp_path):
    old=dict(name='old',path='stt/old.onnx',sha256='a'*64,size=10)
    keep=dict(name='voice',path='tts/model',sha256='b'*64,size=20)
    new=dict(name='new',path='stt/encoder/model.onnx',sha256='c'*64,size=30)
    cfg=dict(id='id.example.voice',version='0.1.0-beta.1',models=[old,keep])
    model=tmp_path/'models.json'
    model.write_text(json.dumps(dict(id=cfg['id'],version=cfg['version'],models=[new,keep])))
    index=dict(models=[dict(path=r['path']) for r in cfg['models']],distribution_review_complete=True,open_items=[])
    files={}
    for name in ('NOTICE','NVIDIA-OPEN-MODEL-LICENSE.pdf','parakeet-model-card.md','sortformer-model-card.md'):
        raw=('bound test fixture for '+name).encode();(tmp_path/name).write_bytes(raw)
        files[name]=dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    record=dict(files=files,models={new['path']:dict(bytes=new['size'],sha256=new['sha256'])},
                upstream=[dict(repository='https://example.com/model',revision='d'*40)])
    (tmp_path/'HEARING-REPLACEMENT.json').write_text(json.dumps(record))
    return cfg,index,model,record


def test_changed_hearing_and_terms_are_bound_together(tmp_path):
    cfg,index,model,_=fixture(tmp_path)
    files=hearing_replacement(cfg,index,model,tmp_path)
    assert len(files)==5
    assert [r['path'] for r in cfg['models']]==['stt/encoder/model.onnx','tts/model']
    assert {r['path'] for r in index['models']}=={'stt/encoder/model.onnx','tts/model'}
    assert index['distribution_review_complete'] is False and len(index['open_items'])==1


@pytest.mark.parametrize('damage',['other-component','identity','duplicate','model-hash','terms-missing','terms-changed','provenance'])
def test_refused_replacement_does_not_partially_update_config(tmp_path,damage):
    cfg,index,model,record=fixture(tmp_path)
    data=json.loads(model.read_text())
    if damage=='other-component': data['models'][1]['size']+=1
    if damage=='identity': data['id']='id.example.other'
    if damage=='duplicate': data['models'].append(data['models'][0])
    if damage=='model-hash': record['models']['stt/encoder/model.onnx']['sha256']='e'*64
    if damage=='terms-missing': del record['files']['NOTICE']
    if damage=='terms-changed': (tmp_path/'NOTICE').write_bytes(b'changed')
    if damage=='provenance': record['upstream']=[]
    model.write_text(json.dumps(data));(tmp_path/'HEARING-REPLACEMENT.json').write_text(json.dumps(record))
    before=copy.deepcopy((cfg,index))
    with pytest.raises(ValueError): hearing_replacement(cfg,index,model,tmp_path)
    assert (cfg,index)==before
