"""Older content-addressed release assets stay pinned, not silently re-uploaded."""
import copy

import pytest

from scripts.prove_reused_release_assets import reuse_rows


def sample():
    digest='a'*64
    url='https://github.com/aiii-dot-id/aiios-voice-plugin/releases/download/v0.1.0-beta.5/'+digest+'-weights.bin'
    model=dict(path='stt/weights.bin',url=url,sha256=digest,size=123)
    row=dict(model,release_tag='v0.1.0-beta.5',asset_name=digest+'-weights.bin',
             repository='aiii-dot-id/aiios-voice-plugin',github_asset_digest='sha256:'+digest,
             github_asset_size=123)
    evidence=dict(passed=True,method='anonymous_github_release_asset_digest',rows=[row])
    return model,evidence


def test_exact_older_public_asset_is_reused():
    model,evidence=sample()
    assert reuse_rows([model],evidence,'0.1.0-beta.6')==evidence['rows']


@pytest.mark.parametrize('damage',('digest','size','url','repository','asset_name','missing','duplicate'))
def test_reused_asset_must_match_signed_declaration(damage):
    model,evidence=sample()
    evidence=copy.deepcopy(evidence)
    if damage=='digest':evidence['rows'][0]['github_asset_digest']='sha256:'+'b'*64
    elif damage=='size':evidence['rows'][0]['github_asset_size']=124
    elif damage=='url':evidence['rows'][0]['url']='https://example.com/x'
    elif damage=='repository':evidence['rows'][0]['repository']='somebody/else'
    elif damage=='asset_name':evidence['rows'][0]['asset_name']='wrong.bin'
    elif damage=='missing':evidence['rows']=[]
    else:evidence['rows'].append(dict(evidence['rows'][0]))
    with pytest.raises(ValueError):reuse_rows([model],evidence,'0.1.0-beta.6')


def test_reuse_cannot_redirect_to_foreign_or_current_release():
    model,evidence=sample()
    model['url']=model['url'].replace('aiios-voice-plugin','other-plugin')
    with pytest.raises(ValueError):reuse_rows([model],evidence,'0.1.0-beta.6')
    model,evidence=sample()
    model['url']=model['url'].replace('v0.1.0-beta.5','v0.1.0-beta.6')
    with pytest.raises(ValueError):reuse_rows([model],evidence,'0.1.0-beta.6')


def test_digest_evidence_is_not_misrepresented_as_body_download():
    model,evidence=sample()
    evidence['method']='head_only'
    with pytest.raises(ValueError):reuse_rows([model],evidence,'0.1.0-beta.6')
