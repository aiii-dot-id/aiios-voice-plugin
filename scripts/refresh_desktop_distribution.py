"""Rebind release staging to the executed Metal RC2, preserving all other bytes.

No downloads, uploads, signing, installation, model inference or old-file edits.
The prior notice corpus is reusable only because every bound external library
and model is unchanged. Native engine changes retain their original notices.
"""
import copy
import json
from pathlib import Path

from scripts.prepare_desktop_distribution import (
    ROOT, METAL_PACKAGE_SHA, IDLE_VAD_PACKAGE_SHA, copy_asset, census, emit, put, sha, verify_staging,
)
from scripts.prepare_desktop_release_notices import validate_runtime_archive
from scripts.repackage_native_schemas import read_package

BASE=ROOT/'deliverables/desktop-distribution-staging-20260914-r1/payload'
CURRENT=ROOT/'deliverables/checkpoints/desktop-metal-unified-20260914-r1'


def validate_replacement(old_manifest,old,new_manifest,new):
    """Only the specifically qualified Mac variant may change at this seam."""
    if set(old)!=set(new):raise ValueError('package members changed')
    old_variants={v['variant_id']:v for v in old_manifest['variants']}
    new_variants={v['variant_id']:v for v in new_manifest['variants']}
    if set(old_variants)!=set(new_variants):raise ValueError('platform set changed')
    mac='macos-arm64-native'
    for key in old_variants:
        if key!=mac and (old_variants[key]!=new_variants[key] or old[old_variants[key]['entrypoint']]!=new[new_variants[key]['entrypoint']]):
            raise ValueError('non-Mac variant changed')
    exceptions={'runtime.json','accelerator.json',old_variants[mac]['entrypoint']}
    for name in old:
        if name not in exceptions and old[name]!=new[name]:raise ValueError('non-runtime payload changed: '+name)
    left={r['variant_id']:r for r in json.loads(old['runtime.json'])['runtimes']}
    right={r['variant_id']:r for r in json.loads(new['runtime.json'])['runtimes']}
    if set(left)!=set(right) or any(left[k]!=right[k] for k in left if k!=mac):
        raise ValueError('non-Mac runtime changed')
    left=json.loads(old['accelerator.json']);right=json.loads(new['accelerator.json'])
    if set(left)!=set(right) or any(left[k]!=right[k] for k in left if k!=mac):
        raise ValueError('non-Mac accelerator changed')
    allowed={'backend','memory_bytes','runtime_libraries'}
    if {k:v for k,v in left[mac].items() if k not in allowed}!={k:v for k,v in right[mac].items() if k not in allowed}:
        raise ValueError('Mac model/operator contract changed')
    if right[mac]['backend']!='metal' or right[mac]['memory_bytes']!=8*1024**3:
        raise ValueError('Mac execution/budget differs from qualified checkpoint')


def stage_current(out):
    oldplan=verify_staging(BASE)
    oldm,old=read_package(BASE/oldplan['parent_bundle'],oldplan['parent_sha256'])
    hand=json.loads((CURRENT/'handoff.json').read_text());package=CURRENT/hand['bundle']['bundle']
    newm,new=read_package(package,METAL_PACKAGE_SHA)
    qualification=json.loads((CURRENT/'qualification.json').read_text())
    if not qualification['passed'] or qualification['package_sha256']!=METAL_PACKAGE_SHA:
        raise ValueError('current package lacks its executed qualification')
    validate_replacement(oldm,old,newm,new)
    cfg=json.loads((CURRENT/'plugin.json').read_text())
    if cfg['models']!=json.loads(new['models.json']) or cfg['settings']!=json.loads(new['settings.json']) or cfg['runtimes']!=json.loads(new['runtime.json'])['runtimes']:
        raise ValueError('current author configuration differs from package')
    runtime_root=ROOT/'deliverables/macos-metal-tts-20260914-r1/handoff-r3/runtime-tree'
    runtime_manifest=runtime_root/'voice-runtime.json';files=json.loads(runtime_manifest.read_text())['files']
    companion=hand['companions']['macos-arm64-native']
    validate_runtime_archive({'archive':companion,'files':files,'runtime_manifest_sha256':sha(runtime_manifest)})
    # The externally distributed Mac ORT bytes have not changed. All Windows /
    # Ubuntu external binaries and model declarations are byte-identical above.
    index=json.loads((BASE/'author/notices/INDEX.json').read_text())
    ort=[r for r in index['libraries'] if r['platform']=='macos' and r['component']=='onnxruntime']
    if len(ort)!=1 or ort[0]['shipped_sha256']!=files['lib/libonnxruntime.1.dylib']['sha256']:
        raise ValueError('Mac external-library notice binding changed')
    mac='macos-arm64-native';entry=next(v['entrypoint'] for v in newm['variants'] if v['variant_id']==mac)
    for v in cfg['variants']:v['artifact']='payloads/'+v['variant_id']
    oldasset=oldplan['runtimes'][mac]['asset']
    replacements={oldplan['parent_bundle'],'release-plan.json','staging-inventory.json',
                  'author/plugin.json','author/payloads/'+mac,'assets/'+oldasset}
    out.mkdir(parents=True,exist_ok=False)
    before=census(BASE)
    for name,row in before.items():
        if name not in replacements:copy_asset(BASE/name,out/name,row['size'],row['sha256'])
    put(out/package.name,package.read_bytes());put(out/'author/payloads'/mac,new[entry]);emit(out/'author/plugin.json',cfg)
    asset=companion['sha256']+'-'+mac+'-runtime.tar.gz'
    copy_asset(companion['path'],out/'assets'/asset,companion['size'],companion['sha256'])
    plan=copy.deepcopy(oldplan);plan.update(parent_bundle=package.name,parent_sha256=METAL_PACKAGE_SHA,
        predecessor_staging_sha256=sha(BASE/'staging-inventory.json'),
        qualified_source_report_sha256=sha(CURRENT/'qualification.json'),
        refreshed_component='Mac native Metal; no model, external dependency or non-Mac variant change')
    del plan['assets'][oldasset]
    plan['assets'][asset]={'kind':'runtime','size':companion['size'],'sha256':companion['sha256'],'platforms':[mac]}
    plan['runtimes'][mac]={'asset':asset}
    emit(out/'release-plan.json',plan);emit(out/'staging-inventory.json',census(out))
    if census(BASE)!=before:raise ValueError('predecessor staging changed')
    verify_staging(out)
    return plan


def stage_vad_checkpoint(out):
    """Explicit private checkpoint selection; do not silently replace Metal RC2."""
    from scripts.package_idle_vad_desktop_family import REPAIR, HANDOFF_SHA, CHANGES, profiles_equal_except_images, delta
    import tarfile
    source=ROOT/'deliverables/desktop-metal-distribution-20260914-r1/payload'
    current=ROOT/'deliverables/checkpoints/desktop-idle-vad-20260914-r1'
    expected=IDLE_VAD_PACKAGE_SHA
    prior=verify_staging(source)
    oldm,old=read_package(source/prior['parent_bundle'],prior['parent_sha256'])
    hand=json.loads((current/'handoff.json').read_text());package=current/hand['bundle']['bundle']
    newm,new=read_package(package,expected);cfg=json.loads((current/'plugin.json').read_text())
    package_audit=json.loads((current/'independent-package-audit-r1.json').read_text())
    if not package_audit['passed'] or package_audit['bundle_sha256']!=expected or not hand['passed']:
        raise ValueError('new checkpoint package has not passed its readback')
    if sha(REPAIR/'desktop-runtime-handoff-r1.json')!=HANDOFF_SHA or hand['combined_execution_handoff_sha256']!=HANDOFF_SHA:
        raise ValueError('executed runtimes do not bind this checkpoint')
    variants={v['variant_id']:v for v in newm['variants']}
    carriers={key:new[v['entrypoint']] for key,v in variants.items()}
    delta(oldm,old,newm,new,cfg,carriers)
    for variant in cfg['variants']:variant['artifact']='payloads/'+variant['variant_id']
    # Every external dependency is unchanged: exact before/after runtime member
    # comparison admits only the previously executed Session/worker images.
    platforms={'macos-arm64-native':'mac','linux-x86_64-native':'linux','windows-x86_64-native':'windows'}
    for key,platform in platforms.items():
        oldarchive=source/'assets'/prior['runtimes'][key]['asset'];companion=hand['companions'][key]
        with tarfile.open(oldarchive) as tf:oldprofile=json.load(tf.extractfile('runtime/voice-runtime.json'))
        with tarfile.open(companion['path']) as tf:newprofile=json.load(tf.extractfile('runtime/voice-runtime.json'))
        profiles_equal_except_images(oldprofile,newprofile,CHANGES[platform])
        validate_runtime_archive(dict(archive=companion,files=newprofile['files'],
                                      runtime_manifest_sha256=hand['execution'][key]['runtime_manifest_sha256']))
    replacements={prior['parent_bundle'],'release-plan.json','staging-inventory.json','author/plugin.json'}
    replacements|={'author/payloads/'+k for k in platforms}
    replacements|={'assets/'+prior['runtimes'][k]['asset'] for k in platforms}
    before=census(source);out.mkdir(parents=True,exist_ok=False)
    for name,row in before.items():
        if name not in replacements:copy_asset(source/name,out/name,row['size'],row['sha256'])
    put(out/package.name,package.read_bytes());emit(out/'author/plugin.json',cfg)
    for key,raw in carriers.items():put(out/'author/payloads'/key,raw)
    plan=copy.deepcopy(prior)
    for key in platforms:
        companion=hand['companions'][key];asset=companion['sha256']+'-'+key+'-runtime.tar.gz'
        copy_asset(companion['path'],out/'assets'/asset,companion['size'],companion['sha256'])
        del plan['assets'][plan['runtimes'][key]['asset']]
        plan['assets'][asset]=dict(kind='runtime',size=companion['size'],sha256=companion['sha256'],platforms=[key])
        plan['runtimes'][key]={'asset':asset}
    plan.update(parent_bundle=package.name,parent_sha256=expected,predecessor_staging_sha256=sha(source/'staging-inventory.json'),
                qualified_source_report_sha256=sha(current/'independent-package-audit-r1.json'),
                execution_handoff_sha256=HANDOFF_SHA,profile='idle-vad-checkpoint',
                refreshed_component='Three privately executed VAD-repair runtimes; no public or human-level promotion')
    emit(out/'release-plan.json',plan);emit(out/'staging-inventory.json',census(out))
    if census(source)!=before:raise ValueError('predecessor staging changed')
    verify_staging(out)
    return plan
