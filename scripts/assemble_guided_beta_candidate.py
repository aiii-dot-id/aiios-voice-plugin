"""Assemble explicitly bound three-desktop bytes for package/integration testing.

Authenticode status comes from the supplied staging receipt, never a filename.
T3 signing, host UID integration and installed journeys remain separate gates.
No release upload or installed identity is changed by this command.
"""
import argparse
import copy
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import tarfile
import zipfile

from scripts.build_plugin_carrier import ROOT, SDK_SOURCE, verify_sdk
from scripts.prepare_desktop_distribution import copy_asset, emit, put, sha
from scripts.repackage_native_schemas import read_package
from scripts.package_common_native_checkpoint import enrollment_interfaces
from scripts.stage_qualified_runtime import check_archive
from scripts.intel_openmp_redist import verified_notices as openmp_notices

TEMPLATE=ROOT/'deliverables/desktop-beta-release-20260915-r1/portable-windows-family-r1/author'
TEMPLATE_SHA='d6c0cee9feab43bf6c07a79ad870966a10cff342d9b0406b2e8b4297e50a9a7b'
PLATFORMS = frozenset(('macos', 'linux', 'windows'))
HEARING_REVIEW_ITEM = 'Review the bound native-multitalker model terms and redistribution notices for this release.'


def candidate_inputs(path):
    """Select explicit immutable staging, never guess the latest directory."""
    if path is None:
        raise ValueError('explicit candidate input manifest required; historical stages are not release defaults')
    rows = json.loads(path.read_text())
    if not isinstance(rows, dict) or set(rows) != PLATFORMS:
        raise ValueError('all three desktop bindings required')
    stages, carriers, accelerators = {}, {}, {}
    for platform, row in rows.items():
        if not isinstance(row, dict) or not isinstance(row.get('accelerator'), dict):
            raise ValueError('explicit accelerator declaration required: ' + platform)
        accelerators[platform] = copy.deepcopy(row['accelerator'])
        stages[platform] = Path(row['stage']).resolve()
        carriers[platform] = Path(row['carrier']).resolve()
        if sha(stages[platform] / 'result.json') != row['stage_sha256']:
            raise ValueError('staging result changed: ' + platform)
        if sha(carriers[platform]) != row['carrier_sha256']:
            raise ValueError('carrier changed: ' + platform)
    return stages, carriers, accelerators


def operator_setup(platform):
    """AII OS 0.1.7 consumes the package declaration; no config edit needed."""
    if platform not in PLATFORMS:
        raise ValueError('unsupported desktop setup')
    return {}


def selected_models(models, accelerator, staged):
    """The downloads must be exactly the model bytes used by this runtime."""
    names = accelerator['models']
    by_name = {row['name']: row for row in models}
    if len(by_name) != len(models) or len(set(names)) != len(names):
        raise ValueError('duplicate model declaration or selection')
    if not names or any(name not in by_name for name in names):
        raise ValueError('undeclared model selected')
    selected = [by_name[name] for name in names]
    observed = {row['path']: dict(sha256=row['sha256'], bytes=row['size']) for row in selected}
    if len(observed) != len(selected) or not staged.get('models') or observed != staged['models']:
        raise ValueError('declared downloads differ from qualified model inventory')
    return selected


def release_contract(cfg):
    """Apply agreed metadata without changing models, budgets or setting values."""
    keys = {'stt_language', 'turn_pause_ms', 'capture_limit_minutes', 'vad_threshold',
            'tts_voice', 'tts_language', 'tts_temperature', 'tts_seed'}
    if {v['platform'] for v in cfg['variants']} != PLATFORMS or len(cfg['variants']) != 3:
        raise ValueError('exact desktop variants required')
    if {s['key'] for s in cfg['settings']} != keys or len(cfg['settings']) != len(keys):
        raise ValueError('review scope for changed settings')
    if any(s.get('scope') not in ('hearing', 'speaking', 'session') for s in cfg['settings']):
        raise ValueError('compiled setting scope required; packaging does not invent it')
    for v in cfg['variants']:
        profile = v['accelerator']
        if type(profile.get('memory_bytes')) is not int or profile['memory_bytes'] <= 0:
            raise ValueError('existing positive host reservation required')
        if 'device_memory_bytes' in profile:
            raise ValueError('new device reservation needs independently measured justification')
        if type(profile.get('startup_ms')) is not int or not 1 <= profile['startup_ms'] <= 3600000:
            raise ValueError('explicit bounded startup_ms required')
    # Operator-confirmed release floor (host exchange 20260918-1843): no
    # 0.1.8 release preceded optional input; it carries that and engine-initiated
    # completion. Qualify against the exact capability-bearing host artifact,
    # not an earlier staged build that happens to print the same version.
    cfg['aiios_min_version'] = '0.1.8'


def current_windows_notices(profile, artifact_root=ROOT):
    """Bind vendor attribution to the NuGet binaries actually in the payload."""
    root=artifact_root/'.build/ort-directml-1.24.4-20260915-r1'
    specs=[('onnxruntime-directml.nupkg','57e9f11b73437bef7a309496135d4c1f96b1a8e9ddba60013fa27bfc1d788681',
      'Microsoft.ML.OnnxRuntime.DirectML 1.24.4',
      {'runtimes/win-x64/native/onnxruntime.dll':'bin/onnxruntime.dll',
       'runtimes/win-x64/native/onnxruntime_providers_shared.dll':'bin/onnxruntime_providers_shared.dll'},
      {n:'ort-'+n for n in ('LICENSE','ThirdPartyNotices.txt','Privacy.md')}),
      ('directml.nupkg','4e7cb7ddce8cf837a7a75dc029209b520ca0101470fcdf275c1f49736a3615b9',
       'Microsoft.AI.DirectML 1.15.4',{'bin/x64-win/DirectML.dll':'bin/DirectML.dll'},
       {n:'directml-'+n for n in ('LICENSE.txt','LICENSE-CODE.txt','ThirdPartyNotices.txt')})]
    files={};libraries=[]
    for name,digest,distribution,binaries,notices in specs:
        if sha(root/name)!=digest:raise ValueError('vendor package changed')
        with zipfile.ZipFile(root/name) as z:
            for src,dest in binaries.items():
                raw=z.read(src);h=hashlib.sha256(raw).hexdigest();row=profile['files'][dest]
                if h!=row['sha256'] or len(raw)!=row['bytes']:raise ValueError('vendor binary provenance differs')
                libraries.append(dict(component='onnxruntime' if dest.endswith('/onnxruntime.dll') else Path(dest).name,
                    platform='windows',distribution=distribution,source_sha256=h,shipped_sha256=h,
                    package_sha256=digest,package_member=src,
                    execution_claim='Distribution and byte provenance only; runtime placement is established by the current execution evidence.'))
            for src,dest in notices.items():
                raw=z.read(src);row=profile['files']['resources/notices/directml/'+dest]
                if hashlib.sha256(raw).hexdigest()!=row['sha256'] or len(raw)!=row['bytes']:
                    raise ValueError('vendor notice differs from qualified runtime')
                files['notices/windows-directml-current/'+dest]=raw
    extra,binding=openmp_notices(profile['files']['bin/libiomp5md.dll'],
        artifact_root/'artifacts/intel-openmp-redist-20260917-r1/intelopenmp.redist.win.2025.2.0.756.nupkg')
    files.update(extra);libraries.append(binding)
    return files,libraries


def staged_archive(stage, name):
    archive=Path(name)
    if archive.is_absolute():
        return archive  # Explicit absolute receipts from earlier local stages.
    if not name or archive.name!=name or name in ('.','..') or '\\' in name or ':' in name:
        raise ValueError('runtime archive must be an absolute path or a colocated filename')
    return stage/archive


def runtime(stage):
    result=json.loads((stage/'result.json').read_text())
    if result.get('passed') is not True or result.get('installed') is not False or result.get('published') is not False:
        raise ValueError('expected successful local staging with no installation/publication claim')
    archive=staged_archive(stage,result['runtime_archive']['path'])
    with tarfile.open(archive) as t:
        raw=t.extractfile('runtime/voice-runtime.json').read()
        if hashlib.sha256(raw).hexdigest()!=result['runtime_manifest_sha256']:
            raise ValueError('runtime manifest binding changed')
        profile=json.loads(raw)
    rows={**profile['files'],'voice-runtime.json':dict(bytes=len(raw),sha256=result['runtime_manifest_sha256'],executable=False)}
    check_archive(archive,result['runtime_archive'],rows,windows=profile['platform']=='windows')
    result['runtime_archive']['path']=str(archive.resolve())
    return result,profile


def runtime_settings(result, profile):
    """The declaration travels inside each hash-bound platform runtime."""
    name = 'resources/settings.json'
    row = profile['files'].get(name)
    if not row:
        raise ValueError('runtime settings declaration missing')
    with tarfile.open(result['runtime_archive']['path']) as archive:
        raw = archive.extractfile('runtime/' + name).read()
    if len(raw) != row['bytes'] or hashlib.sha256(raw).hexdigest() != row['sha256']:
        raise ValueError('runtime settings declaration changed')
    settings = json.loads(raw)
    if not isinstance(settings, list):
        raise ValueError('runtime settings declaration must be a list')
    return settings


def uid_replacement(cfg,index,model_template,notice_root):
    """Change one measured numerical space; keep every other model/term intact."""
    model=json.loads(model_template.read_text());record=json.loads((notice_root/'UID-REPLACEMENT.json').read_text())
    if (model['id'],model['version'])!=(cfg['id'],cfg['version']):raise ValueError('UID template identity differs')
    before={m['path']:m for m in cfg['models']};after={m['path']:m for m in model['models']}
    if len(after)!=len(model['models']) or set(before)!=set(after):raise ValueError('UID template model census differs')
    if {n for n in before if before[n]!=after[n]}!={'uid/model.onnx'}:raise ValueError('replacement changes other models')
    old,new=before['uid/model.onnx'],after['uid/model.onnx']
    if (new['name']!=old['name'] or new['sha256']!=record['model_sha256'] or new['size']!=record['model_bytes']
            or old['sha256']!=record['replaces_model_sha256']):raise ValueError('UID model/notice binding differs')
    files={}
    for name,row in record['files'].items():
        path=Path(name)
        if path.is_absolute() or '..' in path.parts:raise ValueError('unsafe UID notice path')
        raw=(notice_root/path).read_bytes()
        if len(raw)!=row['bytes'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('UID notice changed')
        files['notices/uid-resnet152-lm/'+name]=raw
    files['notices/uid-resnet152-lm/UID-REPLACEMENT.json']=(notice_root/'UID-REPLACEMENT.json').read_bytes()
    updated_index=copy.deepcopy(index)
    rows=[r for r in updated_index['models'] if r['path']=='uid/model.onnx']
    if len(rows)!=1 or rows[0]['sha256']!=old['sha256']:raise ValueError('old UID notice index differs')
    rows[0].update(sha256=new['sha256'],bytes=new['size'],notice_group='uid-resnet152-lm')
    obsolete=[s for s in updated_index['open_items'] if s.startswith('WeSpeaker delegates model licensing to training datasets; VoxBlink2 ')]
    if len(obsolete)!=1:raise ValueError('expected explicit old UID disposition item')
    updated_index['open_items']=[s for s in updated_index['open_items'] if s not in obsolete]
    updated_index['uid_replacement']=dict(model_sha256=new['sha256'],record='notices/uid-resnet152-lm/UID-REPLACEMENT.json',
        old_checkpoint_terms_not_applied_to_new_weights=True,
        declared_terms=record['declarations'],other_component_obligations_unchanged=True)
    # Commit the in-memory pair only after every model/notice/index check passes.
    cfg['models']=copy.deepcopy(model['models'])
    index.clear()
    index.update(updated_index)
    return files


def hearing_replacement(cfg, index, model_template, notice_root):
    """Replace recognition downloads and their notices as one checked unit."""
    model = json.loads(model_template.read_text())
    record_path = notice_root/'HEARING-REPLACEMENT.json'
    record = json.loads(record_path.read_text())
    if (model['id'], model['version']) != (cfg['id'], cfg['version']):
        raise ValueError('hearing template identity differs')
    before = {m['path']: m for m in cfg['models']}
    after = {m['path']: m for m in model['models']}
    if len(after) != len(model['models']) or len(before) != len(cfg['models']):
        raise ValueError('duplicate hearing model path')
    if ({k:v for k,v in before.items() if not k.startswith('stt/')} !=
            {k:v for k,v in after.items() if not k.startswith('stt/')}):
        raise ValueError('hearing replacement changes another component')
    hearing = {k:dict(sha256=v['sha256'], bytes=v['size']) for k,v in after.items() if k.startswith('stt/')}
    if not hearing or hearing != record['models'] or not record.get('upstream'):
        raise ValueError('hearing notice inventory differs')
    required = {'NOTICE', 'NVIDIA-OPEN-MODEL-LICENSE.pdf', 'parakeet-model-card.md', 'sortformer-model-card.md'}
    if set(record['files']) != required:
        raise ValueError('complete hearing terms and model cards required')
    files = {}
    for name, row in record['files'].items():
        raw = (notice_root/name).read_bytes()
        if len(raw) != row['bytes'] or hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise ValueError('hearing notice changed')
        files['notices/native-multitalker/'+name] = raw
    files['notices/native-multitalker/HEARING-REPLACEMENT.json'] = record_path.read_bytes()
    updated = copy.deepcopy(index)
    updated['models'] = [r for r in updated['models'] if not r['path'].startswith('stt/')]
    updated['models'].extend(dict(path=name, **row, notice_group='native-multitalker') for name,row in hearing.items())
    updated['hearing_replacement'] = record
    # A prior model's legal disposition cannot qualify these different weights.
    updated['distribution_review_complete'] = False
    updated['open_items'].append(HEARING_REVIEW_ITEM)
    cfg['models'] = copy.deepcopy(model['models'])
    index.clear(); index.update(updated)
    return files


def apply_hearing_disposition(index, cfg, notices, source, digest):
    """Resolve only this export's review; never inherit unrelated clearance."""
    if sha(source) != digest:
        raise ValueError('hearing disposition changed')
    review = json.loads(source.read_text())
    record = index.get('hearing_replacement', {})
    models = {m['path']: dict(sha256=m['sha256'], bytes=m['size'])
              for m in cfg['models'] if m['path'].startswith('stt/')}
    packed = {n['path']: dict(sha256=n['sha256'], bytes=n['size']) for n in notices}
    expected = {'notices/native-multitalker/' + name: row for name, row in record.get('files', {}).items()}
    record_notice = packed.get('notices/native-multitalker/HEARING-REPLACEMENT.json', {})
    if (review.get('engineering_distribution_review') != 'bound_hearing_terms_reviewed'
            or review.get('resolved_item') != HEARING_REVIEW_ITEM
            or index['open_items'].count(HEARING_REVIEW_ITEM) != 1
            or not models or models != record.get('models') or models != review.get('models')
            or review.get('record_sha256') != record_notice.get('sha256')
            or not expected or review.get('notices') != expected
            or any(packed.get(name) != row for name, row in expected.items())
            or not review.get('reasoning') or not review.get('limitations')):
        raise ValueError('hearing disposition scope or bytes differ')
    index['open_items'] = [s for s in index['open_items'] if s != HEARING_REVIEW_ITEM]
    index['hearing_distribution_disposition'] = dict(source_sha256=digest, **review)
    # The remaining components still require their independently bound review.


def apply_distribution_disposition(index, cfg, profiles, notices, source, digest):
    """Carry a named prior decision only over its unchanged components/notices.

    This does not inherit a prior package signature, installation or release
    status. Reproducibility limitations stay visible, not reopened as licensing
    blockers merely because another runtime image was rebuilt.
    """
    if sha(source) != digest:
        raise ValueError('distribution disposition changed')
    prior=json.loads(source.read_text())
    if (prior.get('passed') is not True or prior.get('engineering_distribution_review')!='named_items_resolved'
            or set(prior.get('resolved_items',[]))!=set(index['open_items'])
            or prior.get('reproducibility_limits_retained')!=prior['resolved_items']):
        raise ValueError('distribution disposition scope differs')
    components=prior.get('components',[])
    expected={'vad/model.onnx','endpoint/model.onnx','windows/bin/asmjit.dll'}
    if len(components)!=len(expected) or {row['component'] for row in components}!=expected:
        raise ValueError('distribution component census differs')
    models={row['path']:row for row in cfg['models']}
    notice_hashes={row['sha256'] for row in notices}
    for row in components:
        if row['component'].startswith('windows/'):
            actual=profiles['windows']['files'].get(row['component'].removeprefix('windows/'),{})
        else:
            actual=models.get(row['component'],{})
        if actual.get('sha256')!=row['sha256'] or row['notice_sha256'] not in notice_hashes:
            raise ValueError('reviewed component or notice changed: '+row['component'])
    index['distribution_review_complete']=True
    index['open_items']=[]
    index['reproducibility_limits']=list(prior['reproducibility_limits_retained'])
    index['distribution_disposition']=dict(source_sha256=digest,
        source_package_sha256=prior['signed_package_sha256'],components=copy.deepcopy(components),
        reasoning=list(prior['reasoning']),scope=prior['scope'],
        previous_signature_or_installation_inherited=False)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--inputs',type=Path,required=True,help='Explicit stage/carrier SHA-256 bindings and per-platform accelerator declarations')
    p.add_argument('--uid-model-template',type=Path)
    p.add_argument('--uid-notices',type=Path)
    p.add_argument('--uid-model-template-sha256')
    p.add_argument('--uid-notices-sha256')
    p.add_argument('--hearing-model-template',type=Path)
    p.add_argument('--hearing-model-template-sha256')
    p.add_argument('--hearing-notices',type=Path)
    p.add_argument('--hearing-notices-sha256')
    p.add_argument('--hearing-disposition',type=Path)
    p.add_argument('--hearing-disposition-sha256')
    p.add_argument('--distribution-addendum',type=Path)
    p.add_argument('--distribution-addendum-sha256')
    p.add_argument('--version',help='New immutable release version; never overwrite a published tag')
    p.add_argument('--artifact-root',type=Path,default=ROOT,help='Explicit retained artifact store, never a source checkout override')
    p.add_argument('--go-modcache',type=Path,required=True)
    a=p.parse_args();out=a.out.resolve();pin,_=verify_sdk()
    artifact_root=a.artifact_root.resolve()
    template=artifact_root/TEMPLATE.relative_to(ROOT)
    stages, carriers, accelerators = candidate_inputs(a.inputs)
    if sha(template/'plugin.json')!=TEMPLATE_SHA:raise ValueError('template changed')
    cfg=json.loads((template/'plugin.json').read_text())
    # Notice selection and model selection form one atomic packaging decision.
    index=json.loads((template/'notices/INDEX.json').read_text());uid_files={}
    if any((a.uid_model_template,a.uid_notices,a.uid_model_template_sha256,a.uid_notices_sha256)):
        if not all((a.uid_model_template,a.uid_notices,a.uid_model_template_sha256,a.uid_notices_sha256)):raise ValueError('complete UID replacement bindings required')
        if sha(a.uid_model_template)!=a.uid_model_template_sha256 or sha(a.uid_notices/'UID-REPLACEMENT.json')!=a.uid_notices_sha256:raise ValueError('UID replacement inputs changed')
        uid_files=uid_replacement(cfg,index,a.uid_model_template,a.uid_notices)
    hearing_files = {}
    if any((a.hearing_model_template,a.hearing_model_template_sha256,a.hearing_notices,a.hearing_notices_sha256)):
        if not all((a.hearing_model_template,a.hearing_model_template_sha256,a.hearing_notices,a.hearing_notices_sha256)):
            raise ValueError('complete hearing replacement bindings required')
        if sha(a.hearing_model_template)!=a.hearing_model_template_sha256 or sha(a.hearing_notices/'HEARING-REPLACEMENT.json')!=a.hearing_notices_sha256:
            raise ValueError('hearing replacement inputs changed')
        hearing_files=hearing_replacement(cfg,index,a.hearing_model_template,a.hearing_notices)
    if a.version:
        if not re.fullmatch(r'\d+\.\d+\.\d+-beta\.\d+',a.version):raise ValueError('expected explicit beta version')
        old_base='https://github.com/aiii-dot-id/aiios-voice-plugin/releases/download/v'+cfg['version']+'/'
        new_base='https://github.com/aiii-dot-id/aiios-voice-plugin/releases/download/v'+a.version+'/'
        for model in cfg['models']:
            if model['url'].startswith(old_base):model['url']=new_base+model['url'][len(old_base):]
        cfg['version']=a.version
    # Signature/readiness status belongs in evidence, not descriptive metadata
    # that would remain falsely "unsigned" after the exact package is signed.
    cfg['title']='AII Voice'
    cfg['description']='On-device English speech for macOS, Windows and Ubuntu: ten selectable voices, speaker-separated recognition, adjustable VAD, interruption/recovery and durable anonymous speaker UUIDs with later naming. Speaker attribution is a model estimate, not authentication or authority.'
    cfg['runtimes']=[]
    bound={};profiles={}
    settings = None
    for platform,stage in stages.items():
        bound[platform],profiles[platform]=runtime(stage)
        if sha(carriers[platform])!=bound[platform]['carrier_sha256']:raise ValueError('carrier changed')
        declared = runtime_settings(bound[platform], profiles[platform])
        if settings is not None and declared != settings:
            raise ValueError('desktop runtime settings declarations disagree')
        settings = declared
    cfg['settings'] = settings
    for variant in cfg['variants']:
        variant['accelerator'] = accelerators[variant['platform']]
    release_contract(cfg)
    descriptors=json.loads(subprocess.check_output([str(carriers['macos'])],env={'PATH':'','AIISDK_DESCRIBE':'1'},timeout=10))
    cfg['interfaces']=enrollment_interfaces(descriptors)
    schemas={d[k] for d in descriptors for k in ('input','output') if d.get(k)}
    expected_schemas={'schemas/speaker-'+name+'.input.json' for name in
        ('list','enroll','remove','reset','discard_capture','upgrade_policy','buckets','associate','forget')}
    expected_schemas.update(('schemas/speaker.output.json','schemas/speaker-buckets.output.json'))
    if schemas!=expected_schemas:raise ValueError('complete speaker schema set required')
    out.mkdir(parents=True,exist_ok=False);author=out/'author'
    assets={};plans={}
    for v in cfg['variants']:
        platform=v['platform'];r=bound[platform];variant=v['variant_id']
        if variant!=r['variant_id']:raise ValueError('variant binding differs')
        v['artifact']='payloads/'+variant
        copy_asset(carriers[platform],author/v['artifact'],carriers[platform].stat().st_size,r['carrier_sha256'])
        arc=r['runtime_archive'];name=arc['sha256']+'-'+variant+'-runtime.tar.gz'
        copy_asset(arc['path'],out/'assets'/name,arc['size'],arc['sha256'])
        decl={k:arc[k] for k in ('sha256','size','files','installed_bytes','inventory_sha256')}
        decl.update(variant_id=variant,url='https://github.com/aiii-dot-id/aiios-voice-plugin/releases/download/v'+cfg['version']+'/'+name)
        cfg['runtimes'].append(decl);assets[name]=dict(kind='runtime',variant_id=variant,sha256=arc['sha256'],size=arc['size'])
        # Do not change measured model/backend/resource choices with packaging.
        selected=selected_models(cfg['models'],v['accelerator'],r)
        if ('endpoint/windows/coefficients.f32' in {m['path'] for m in selected})!=(platform=='windows'):
            raise ValueError('foreign platform endpoint model selected')
        plans[platform]=dict(variant_id=variant,runtime=decl,carrier_sha256=r['carrier_sha256'],
            carrier_path=str((author/v['artifact']).resolve()),models=selected,
            runtime_archive_path=str((out/'assets'/name).resolve()),
            operator_config_merge=operator_setup(platform))
    # These are setup instructions using existing host keys, not a new SDK
    # declaration and not permission to replace a whole identity config.
    emit(out/'operator-setup.json',dict(
        scope='Operator-reviewed merge into existing host config; never replace config.json. No plugin self-authorization.',
        restart_required=False,
        platforms={p:operator_setup(p) for p in plans},
        rationale='AII OS consumes the explicit per-platform startup allowance subject to operator ceilings and overrides. memory_bytes is a declared host reservation, not a measured peak. Unmeasured device memory is omitted, not zero.',
        automatic_configuration=False))
    # Notices are original texts with exact attribution. Rebind the one stale
    # execution description; do not represent notice collection as clearance.
    notice_rows=json.loads((template/'release-notices.json').read_text())
    if uid_files:notice_rows=[r for r in notice_rows if not r['path'].startswith('notices/wespeaker-uid/')]
    for row in notice_rows:
        if row['path']!='notices/INDEX.json':
            copy_asset(template/row['path'],author/row['path'],row['size'],row['sha256'])
    # The original index describes third-party bytes, which must be present in
    # this candidate. A missing or changed library is not a reusable notice.
    files,current=current_windows_notices(profiles['windows'],artifact_root)
    files.update(uid_files)
    files.update(hearing_files)
    index['libraries']=[r for r in index['libraries'] if not (r['platform']=='windows' and r['component']=='onnxruntime')]+current
    # Keep the historical PyPI notice corpus, but select the exact unchanged
    # DLL from Intel's redistributable channel with its own original terms.
    obsolete=[s for s in index['open_items'] if s.startswith('The byte-matched Intel OpenMP 2025.2.0 distribution carries its Developer Tools EULA')]
    if len(obsolete)!=1:raise ValueError('expected historical OpenMP disposition item')
    index['open_items']=[s for s in index['open_items'] if s not in obsolete]
    index['intel_openmp_distribution']=copy.deepcopy(current[-1])
    notice_rows=[r for r in notice_rows if r['path']!='notices/INDEX.json']
    for name,raw in files.items():
        put(author/name,raw)
        notice_rows.append(dict(path=name,sha256=hashlib.sha256(raw).hexdigest(),size=len(raw)))
    index['original_notice_files']=copy.deepcopy(notice_rows)
    index['windows_current_distribution']='NuGet native distribution, not the previous Python wheel; the original notice corpus is retained, with exact currently shipped notices under notices/windows-directml-current.'
    for lib in index['libraries']:
        hashes={f['sha256'] for f in profiles[lib['platform']]['files'].values()}
        if lib['shipped_sha256'] not in hashes:raise ValueError('third-party notice binding changed: '+lib['component'])
    # This sentence belonged to the original notice collection, not to this
    # package. The bytes above now include its notices and declared endpoints.
    # Actual public availability and legal disposition remain separate gates.
    historical='Final release must incorporate notices and real asset URLs before freezing/signing; this evidence bundle did not repack the working candidate.'
    if index['open_items'].count(historical)!=1:raise ValueError('notice-collection history changed')
    index['open_items'].remove(historical)
    index['release_notice_packaging']=dict(notices='included_and_byte_bound',
        declared_urls='pinned_upstream_and_new_release_destinations',
        public_release_url_availability='not_asserted_by_assembly',
        supersedes_original_collection_note=historical)
    if a.hearing_disposition or a.hearing_disposition_sha256:
        if not (a.hearing_disposition and a.hearing_disposition_sha256):
            raise ValueError('complete hearing disposition binding required')
        apply_hearing_disposition(index,cfg,notice_rows,a.hearing_disposition,a.hearing_disposition_sha256)
    if a.distribution_addendum or a.distribution_addendum_sha256:
        if not (a.distribution_addendum and a.distribution_addendum_sha256):
            raise ValueError('complete distribution disposition binding required')
        apply_distribution_disposition(index,cfg,profiles,notice_rows,a.distribution_addendum,a.distribution_addendum_sha256)
    emit(author/'notices/INDEX.json',index)
    notice_rows.append(dict(path='notices/INDEX.json',size=(author/'notices/INDEX.json').stat().st_size,sha256=sha(author/'notices/INDEX.json')))
    for name in schemas:put(author/name,(ROOT/'plugin/native'/name).read_bytes())
    emit(author/'plugin.json',cfg);emit(author/'descriptors.json',descriptors)
    emit(author/'release-notices.json',notice_rows)
    env={**os.environ,'GOTOOLCHAIN':'local','GOWORK':'off','GOPROXY':'off','GOSUMDB':'off',
         'GOMODCACHE':str(a.go_modcache.resolve())}
    assembler=out/'assemble'
    for label,cmd in [('build',['/usr/local/go1.27/bin/go','build','-trimpath','-buildvcs=false','-o',str(assembler),str(ROOT/'scripts/private_cp1_package.go')]),
                      ('assemble',[str(assembler),str(author)])]:
        r=subprocess.run(cmd,cwd=SDK_SOURCE,env=env,capture_output=True,timeout=120)
        put(out/(label+'.stdout'),r.stdout);put(out/(label+'.stderr'),r.stderr)
        if r.returncode:raise RuntimeError(label+' failed; retained output')
    assembly=json.loads(r.stdout);manifest,files=read_package(author/assembly['bundle'],assembly['sha256'])
    for v in manifest['variants']:
        if files[v['entrypoint']]!=carriers[v['platform']].read_bytes():raise ValueError('packaged carrier differs')
    if json.loads(files['models.json'])!=cfg['models']:raise ValueError('packaged model union differs')
    if json.loads(files['settings.json'])!=cfg['settings']:raise ValueError('packaged settings differ')
    for name in schemas:
        if files[name]!=(ROOT/'plugin/native'/name).read_bytes():raise ValueError('schema not packed exactly')
    emit(out/'platform-plans.json',plans);emit(out/'release-assets.json',assets)
    emit(out/'result.json',dict(passed=True,scope=__doc__,bundle=assembly,sdk_revision=pin['revision'],
        source_sha256=sha(__file__),assembler_sha256=sha(ROOT/'scripts/private_cp1_package.go'),
        bound_staging={p:sha(s/'result.json') for p,s in stages.items()},variants=list(plans),
        explicit_inputs_sha256=sha(a.inputs) if a.inputs else None,
        speaker_methods=[d['id'] for d in descriptors if d['id'].startswith('speaker.')],
        input_output_schema_files=sorted(schemas),models=len(cfg['models']),notices=len(notice_rows),
        signed=False,installed=False,published=False,beta_release_ready=False,
        release_status=dict(package_integrity='verified',package_signature='not_performed_by_assembly',
            installed_journey='not_performed_by_assembly',technical_acceptance='platform_audits_only',
            distribution_review='complete' if index['distribution_review_complete'] else 'open',publication='not_performed_by_assembly'),
        windows_authenticode_verified=bound['windows'].get('authenticode_verified',False),
        required_before_release=([] if bound['windows'].get('authenticode_verified') else ['Authenticode and rebind Windows runtime/carrier'])+[
          'host guided capture, UID ingress filters and AI-visible policy',
          'final signed fresh-cache installed journeys','authorized T3 signature and host verification',
          'signed catalog and hosted byte readback']+([] if index['distribution_review_complete'] else ['third-party distribution review'])))
    print(json.dumps({'bundle':assembly,'variants':list(plans),'signed':False,'published':False}))


if __name__=='__main__':main()
