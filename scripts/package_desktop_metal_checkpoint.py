"""Replace only the Mac member with the executed Metal checkpoint.

Retains Ubuntu/Windows executable and companion bytes, all models/settings/
schemas, and every release hold. No new native build, signing or installation.
The result is a private three-platform checkpoint, not a public-download release.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from scripts.build_plugin_carrier import ROOT, SDK_SOURCE, verify_sdk
from scripts.repackage_native_schemas import read_package
from scripts.native_checkpoint_binding import verify_checkpoint
from scripts.audit_native_desktop_family import audit as audit_family, executable_binding

PARENT = ROOT/'deliverables/checkpoints/desktop-beta-unified-20260914-r5'
PARENT_SHA = 'd331620b03ed586a5c2764d236af90a48a60caf8365d827296e0fe004f71cabd'
METAL_ROOT = ROOT/'deliverables/macos-metal-tts-20260914-r1'
METAL = METAL_ROOT/'handoff-r3'
KEY = 'macos-arm64-native'


def sha(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def emit(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f: json.dump(value, f, indent=2, ensure_ascii=False); f.write('\n')


def put(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as f: f.write(raw)


def replacement_config(template, receipt, *, version, measured_peak):
    cfg = copy.deepcopy(template)
    if version == cfg['version']: raise ValueError('new checkpoint version required')
    if measured_peak <= 0 or measured_peak > 8*1024**3:
        raise ValueError('observed Mac memory does not fit the explicit 8 GiB admission budget')
    cfg['version'] = version
    cfg['title'] = 'AII Voice — accelerated desktop checkpoint'
    for v in cfg['variants']:
        v['artifact'] = 'payloads/'+v['variant_id']
        if v['variant_id'] == KEY:
            if v['accelerator']['backend'] != 'cpu': raise ValueError('unexpected parent Mac backend')
            v['accelerator'].update(backend='metal', memory_bytes=8*1024**3)
            v['accelerator']['runtime_libraries'].append('Metal')
    for r in cfg['runtimes']:
        if r['variant_id'] == KEY:
            for k in ('files', 'installed_bytes', 'inventory_sha256', 'sha256', 'size'): r[k] = receipt[k]
            # Not a release URL. Never mislabel the old CPU URL as this runtime.
            r['url'] = 'https://checkpoint.invalid/runtime/macos-arm64-metal-'+receipt['sha256']+'.tar.gz'
    return cfg


def audit_delta(old, before, new, after, expected_config, mac_carrier):
    old_variants = {v['variant_id']:v for v in old['variants']}
    if set(old_variants) != {v['variant_id'] for v in new['variants']}:
        raise ValueError('platform set changed')
    allowed = {old_variants[KEY]['entrypoint'], 'accelerator.json', 'runtime.json'}
    if set(after) != set(before) or any(after[n] != raw for n,raw in before.items() if n not in allowed):
        raise ValueError('unchanged desktop payload or shared contract changed')
    expected_manifest = copy.deepcopy(old)
    expected_manifest.update(version=expected_config['version'], title=expected_config['title'], package_hash=new['package_hash'])
    for v in expected_manifest['variants']:
        if v['variant_id'] == KEY: v['artifact_hash'] = 'sha256:'+hashlib.sha256(mac_carrier).hexdigest()
    if expected_manifest != new: raise ValueError('unexpected package authority or interface delta')
    if after[old_variants[KEY]['entrypoint']] != mac_carrier: raise ValueError('Mac carrier differs from executed checkpoint')
    if json.loads(after['runtime.json'])['runtimes'] != expected_config['runtimes']:
        raise ValueError('runtime declaration differs')
    if json.loads(after['accelerator.json']) != {v['variant_id']:v['accelerator'] for v in expected_config['variants']}:
        raise ValueError('accelerator declaration differs')


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--out', type=Path, required=True)
    p.add_argument('--version', required=True); a = p.parse_args(); out = a.out.resolve()
    pin, _ = verify_sdk()
    parent = json.loads((PARENT/'handoff.json').read_text())
    old, before = read_package(PARENT/parent['bundle']['bundle'], PARENT_SHA)
    hand = json.loads((METAL/'handoff.json').read_text())
    proof = json.loads((METAL_ROOT/'independent-audit-r1.json').read_text())
    if not hand['passed'] or not proof['passed'] or not proof['correctness_passed'] or not proof['performance_passed']:
        raise ValueError('Metal full development gate not passed')
    if sha(METAL_ROOT/'independent-audit-r1.json') != hand['audit_sha256']:
        raise ValueError('Metal audit changed')
    for n,h in hand['archives'].items():
        if sha(METAL/n) != h: raise ValueError('sealed Metal handoff changed: '+n)
    origin = METAL_ROOT/'session-r6/checkpoint'
    frozen, record, _, bindings = verify_checkpoint(origin)
    if parent['sdk_revision'] != hand['sdk_revision'] or hand['sdk_revision'] != pin['revision']:
        raise ValueError('different SDK revisions')
    carrier = (METAL/'aii-voice-t3').read_bytes()
    if hashlib.sha256(carrier).hexdigest() != hand['carrier_sha256'] or carrier != (origin/'runtime/aii-voice-t3').read_bytes():
        raise ValueError('different Mac carrier')
    by_name = {m['name']:m for m in json.loads(before['models.json'])}
    selected = json.loads(before['accelerator.json'])[KEY]['models']
    wanted = {by_name[n]['path']:{'sha256':by_name[n]['sha256'], 'bytes':by_name[n]['size']} for n in selected}
    if frozen['models'] != wanted: raise ValueError('Metal models differ from desktop declaration')
    if json.loads((METAL/'settings.json').read_text()) != json.loads(before['settings.json']):
        raise ValueError('Metal operator settings differ')
    peak = max(proof['arms'][n]['sampled_peak_rss_bytes'] for n in ('2-metal','3-metal'))
    cfg = replacement_config(json.loads((PARENT/'plugin.json').read_text()), hand['runtime'], version=a.version, measured_peak=peak)
    out.mkdir(parents=True, exist_ok=False)
    variants = {v['variant_id']:v for v in old['variants']}
    for key,v in variants.items(): put(out/'payloads'/key, carrier if key == KEY else before[v['entrypoint']])
    descriptors = []
    for i in old['interfaces']['core']: descriptors.extend(json.loads(before[f'interfaces/{i["id"]}.v{i["version"]}.schema.json']))
    for n,raw in before.items():
        if n.startswith('schemas/'): put(out/n, raw)
    emit(out/'plugin.json', cfg); emit(out/'descriptors.json', sorted(descriptors,key=lambda d:d['id']))
    desc = subprocess.run([str(METAL/'aii-voice-t3')], env={**os.environ,'AIISDK_DESCRIBE':'1'}, capture_output=True, timeout=10)
    put(out/'describe.stdout', desc.stdout); put(out/'describe.stderr', desc.stderr)
    if desc.returncode or json.loads(desc.stdout) != sorted(descriptors,key=lambda d:d['id']):
        raise ValueError('executed carrier Describe differs')
    env = {**os.environ,'GOTOOLCHAIN':'local','GOWORK':'off','GOPROXY':'off'}
    for name, command in (
        ('build', ['/usr/local/go1.27/bin/go','build','-trimpath','-buildvcs=false','-o',str(out/'assemble'),str(ROOT/'scripts/private_cp1_package.go')]),
        ('assemble',[str(out/'assemble'),str(out)])):
        run = subprocess.run(command,cwd=SDK_SOURCE,env=env,capture_output=True,timeout=90)
        put(out/(name+'.stdout'),run.stdout); put(out/(name+'.stderr'),run.stderr)
        if run.returncode: raise ValueError(name+' failed; evidence retained')
    assembly = json.loads(run.stdout)
    new,after = read_package(out/assembly['bundle'], assembly['sha256'])
    audit_delta(old,before,new,after,cfg,carrier)
    stage = out/'bound'/KEY
    shutil.copytree(origin/'runtime', stage/'runtime')
    put(stage/'carrier-build.json',(origin/'carrier-build.json').read_bytes())
    frozen = {**frozen,'package_sha256':assembly['sha256'],'parent_checkpoint':str(origin),'parent_freeze_sha256':sha(origin/'freeze.json')}
    emit(stage/'freeze.json', frozen)
    verify_checkpoint(stage)
    companions = copy.deepcopy(parent['companions'])
    companions[KEY] = {**next(r for r in cfg['runtimes'] if r['variant_id']==KEY), 'path':str(METAL/'macos-arm64-metal-runtime.tar.gz')}
    bound = copy.deepcopy(parent['bound_carriers'])
    bound[KEY] = {'record':str(stage/'carrier-build.json'), 'record_sha256':sha(stage/'carrier-build.json'),
                  'carrier_sha256':hand['carrier_sha256'],'runtime_manifest_sha256':hand['runtime_manifest_sha256']}
    result = {k:parent[k] for k in ('host_installation_blocker','installed_host_gate_passed','ready_for_private_test_signing_review')}
    result.update(passed=True, scope=__doc__, bundle=assembly, version=a.version, sdk_revision=pin['revision'],
        companions=companions, bound_carriers=bound, variants=sorted(variants), parent_package_sha256=PARENT_SHA,
        parent_handoff_sha256=sha(PARENT/'handoff.json'), metal_handoff_sha256=sha(METAL/'handoff.json'),
        metal_evidence_archive_sha256=hand['archives']['evidence.tar.gz'], metal_audit_sha256=hand['audit_sha256'],
        memory={'observed_peak_rss_bytes':peak,'declared_admission_budget_bytes':8*1024**3,'cold_shader_cost_seconds':7.863,
                'scope':'Measured development peak, not a universal bound. Metal covers TTS; recognizer/VAD/endpoint/UID remain CPU.'},
        signed=False,installed=False,published=False,human_level_qualified=False,public_distribution_ready=False,
        fresh_packaged_mac_speech_pending=True,script_sha256=sha(Path(__file__)),
        inherited_execution={'linux':'byte-identical tested carrier/runtime/models','windows':'byte-identical tested carrier/runtime/models; Windows 11 VM evidence only'},
        public_blockers=['release asset base URLs','notice inventory rebind for changed Mac companion','host isolation closure','signing and installed-browser qualification','human-level and mobile qualification'])
    emit(out/'handoff.json',result)
    audit = audit_family(out, macos_backend='metal')
    emit(out/'independent-package-audit.json',audit)
    for n,h in bindings.items():
        if sha(n) != h: raise ValueError('executed Metal checkpoint changed during packaging')
    if sha(PARENT/parent['bundle']['bundle']) != PARENT_SHA: raise ValueError('parent changed')
    print(json.dumps({'bundle':assembly,'mac_memory':result['memory'],'passed':True,'signed':False}))


if __name__ == '__main__': main()
