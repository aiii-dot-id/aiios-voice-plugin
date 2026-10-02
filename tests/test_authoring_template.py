"""Explicit current metadata must bind its complete notice and file census."""
import json

import pytest

from scripts.assemble_guided_beta_candidate import authoring_template, sha


def fixture(root):
    (root/'notices').mkdir()
    (root/'plugin.json').write_text(json.dumps({'version':'0.1.0-beta.8'}))
    (root/'notices/INDEX.json').write_text(json.dumps({'open_items':['review remains open']}))
    (root/'notices/LICENSE').write_text('Original upstream notice')
    notices=[dict(path=p.relative_to(root).as_posix(),sha256=sha(p),size=p.stat().st_size)
             for p in sorted((root/'notices').iterdir())]
    (root/'release-notices.json').write_text(json.dumps(notices))
    files={p.relative_to(root).as_posix():dict(sha256=sha(p),size=p.stat().st_size)
           for p in root.rglob('*') if p.is_file()}
    receipt=root/'authoring-inputs.json'
    receipt.write_text(json.dumps(dict(schema='aiii.voice.authoring-inputs.v1',files=files)))
    return sha(receipt)


def test_current_metadata_without_historical_artifact_store(tmp_path):
    cfg,index,rows=authoring_template(tmp_path,fixture(tmp_path))
    assert cfg['version']=='0.1.0-beta.8'
    assert index['open_items']==['review remains open']
    assert len(rows)==2


@pytest.mark.parametrize('damage',['receipt','config','index','notice','extra','link','inventory'])
def test_changed_metadata_never_reuses_a_binding(tmp_path,damage):
    digest=fixture(tmp_path)
    if damage=='receipt':digest='0'*64
    elif damage=='config':(tmp_path/'plugin.json').write_text('{}')
    elif damage=='index':(tmp_path/'notices/INDEX.json').write_text('{}')
    elif damage=='notice':(tmp_path/'notices/LICENSE').write_text('changed')
    elif damage=='extra':(tmp_path/'unused').write_text('not declared')
    elif damage=='link':
        p=tmp_path/'notices/LICENSE';p.rename(tmp_path/'other');p.symlink_to(tmp_path/'other')
    else:
        (tmp_path/'release-notices.json').write_text('[]')
        receipt=tmp_path/'authoring-inputs.json';record=json.loads(receipt.read_text())
        p=tmp_path/'release-notices.json'
        record['files']['release-notices.json']=dict(sha256=sha(p),size=p.stat().st_size)
        receipt.write_text(json.dumps(record));digest=sha(receipt)
    with pytest.raises(ValueError):authoring_template(tmp_path,digest)
