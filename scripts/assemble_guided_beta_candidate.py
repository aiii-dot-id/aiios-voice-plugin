"""Assemble explicitly bound desktop component sets for package/integration testing.

Authenticode status comes from the supplied staging receipt, never a filename.
T3 signing, host UID integration and installed journeys remain separate gates.
No release upload or installed identity is changed by this command.
"""
from scripts._assertions import require_assertions
require_assertions()
import argparse
import copy
import hashlib
import json
import os
import platform
import re
from pathlib import Path
import subprocess
import tarfile
from pathlib import PurePosixPath

from scripts.build_plugin_carrier import ROOT, verify_sdk
from scripts.release_files import copy_asset, emit, put, sha
from scripts.repackage_native_schemas import read_package
from scripts.package_common_native_checkpoint import enrollment_interfaces
from scripts.stage_qualified_runtime import check_archive, composition_coordinates
from scripts.verify_release_sdk_compatibility import verify_sdk_source
from scripts.package_native_runtime import refuse_interpreter_profile, safe_relative
from scripts.runtime_limits import profile_limits, startup_covers_readiness
from scripts.check_native_binary_privacy import (
    ALWAYS_FAIL, IMAGE_NAME, package_metadata, release_owned_image)
from scripts.stage_desktop_publication import HEARING_NOTICE_GROUP, separator_model_path
from scripts.export_mossformer2_separator import (
    CHECKPOINT_SHA256 as SEPARATOR_CHECKPOINT_SHA256, UPSTREAM_REVISION as SEPARATOR_UPSTREAM_REVISION)

PLATFORMS = frozenset(('macos', 'linux', 'windows'))
HEARING_REVIEW_ITEM = 'Review the bound native-multitalker model terms and redistribution notices for this release.'
# docs/RUNTIME_COMPONENT_SELECTION.md: a signed variant_preference with
# per-set reservations is honored only from this host release. Packages from
# this assembler always declare that contract, so no host below it is valid.
COMPONENT_SELECTION_MIN_HOST = '0.1.12'
# The SDK's RuntimeExtentMinHost (aii-plugin-sdk f8b4961): the first host that
# reads a runtime declaration's largest_file_bytes and depth. Every earlier
# host refuses those members, and a package at this floor must declare them
# for every runtime. Assembly always declares the extent its stage measured,
# so it requires this host; tests/fixtures/sdk_runtime_extent.json is the
# SDK's own vector file.
RUNTIME_EXTENT_MIN_HOST = '0.1.14'
# The SDK's maxRuntimeDepth: a member path is at most 511 bytes, root included.
MAX_RUNTIME_DEPTH = (511 - 1) // 2
RELEASE_DOWNLOAD = 'https://github.com/aiii-dot-id/aiios-voice-plugin/releases/download/v'


def host_version(value):
    """The host window's own grammar: major.minor.patch decimals, nothing else."""
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value):
        raise ValueError('explicit minimum host release required as major.minor.patch')
    return tuple(int(part) for part in value.split('.'))


# Accelerator domains whose capacity the host measures on each desktop. Apple
# Silicon Metal shares host memory (its footprint is charged to memory_bytes
# with device_memory_bytes=0); Vulkan and CUDA are discrete device budgets.
# A GPU set that omits or relabels its own domain, or declares zero device
# memory for a discrete GPU, would make the host skip the device check.
MEASURED_DOMAINS = {'macos': {'metal': 'unified'},
                    'linux': {'vulkan': 'discrete', 'cuda': 'discrete'},
                    'windows': {'vulkan': 'discrete', 'cuda': 'discrete'}}
RESERVATION_EVIDENCE = 'aiii.voice.accelerator-reservation-evidence.v1'


def archive_extent(declaration, rows):
    """The extent runtime-pack measured must be this archive's own tree.

    The SDK measures every packed file except its generated inventory: the
    largest file's bytes and the deepest path in segments below the root.
    """
    measured = (max(row['bytes'] for row in rows.values()),
                max(len(PurePosixPath(name).parts) for name in rows))
    if (declaration.get('largest_file_bytes'), declaration.get('depth')) != measured:
        raise ValueError('re-stage this runtime to bind its archive extent (largest file '
                         + str(measured[0]) + ' bytes, depth ' + str(measured[1]) + ')')
    return measured


def runtime_declaration(archive, variant, version):
    """The SDK runtime declaration, carrying the extent its stage measured."""
    name = archive['sha256'] + '-' + variant + '-runtime.tar.gz'
    decl = {k: archive[k] for k in ('sha256', 'size', 'files', 'installed_bytes', 'inventory_sha256',
                                    'largest_file_bytes', 'depth')}
    decl.update(variant_id=variant, url=RELEASE_DOWNLOAD + version + '/' + name)
    return name, decl


def runtime_extent(decls, minimum_host_version):
    """The SDK's extent rules for an authored package, exactly.

    An extent requires aiios_min_version >= RUNTIME_EXTENT_MIN_HOST (the
    host's ValidateRuntimeExtent); a package at that floor declares it for
    every runtime (authoring's requireRuntimeExtent); and a declared extent
    is both fields, a largest file between the tree's mean file and its
    installed bytes, and a depth the tree grammar admits (validExtent).
    """
    at_floor = host_version(minimum_host_version) >= host_version(RUNTIME_EXTENT_MIN_HOST)
    for d in decls:
        # JSON null decodes to the SDK's absent pointer.
        largest, depth = d.get('largest_file_bytes'), d.get('depth')
        name = str(d.get('variant_id'))
        if (largest is None) != (depth is None):
            raise ValueError('runtime ' + name + ': largest_file_bytes and depth are declared together or not at all')
        if largest is None:
            if at_floor:
                raise ValueError('runtime ' + name + ': aiios_min_version ' + minimum_host_version
                                 + ' reads a runtime\'s extent; declare largest_file_bytes and depth')
            continue
        if not at_floor:
            raise ValueError('runtime ' + name + ' declares largest_file_bytes and depth, which require '
                             'aiios_min_version >= ' + RUNTIME_EXTENT_MIN_HOST)
        files, installed = d.get('files'), d.get('installed_bytes')
        if any(type(v) is not int for v in (largest, depth, files, installed)) or files < 1 or installed < 1:
            raise ValueError('runtime ' + name + ': extent and inventory must be integers')
        mean = (installed - 1) // files + 1  # the smallest a largest file can be
        if largest <= 0 or largest > installed or largest < mean:
            raise ValueError('runtime ' + name + ': largest_file_bytes ' + str(largest) + ' is not the largest of '
                             + str(files) + ' files totalling ' + str(installed) + ' bytes')
        if not 1 <= depth <= MAX_RUNTIME_DEPTH:
            raise ValueError('runtime ' + name + ': depth ' + str(depth) + ' is outside 1..' + str(MAX_RUNTIME_DEPTH))


def authoring_extent_floor(sdk):
    """The verified authoring SDK must read the extent at the same floor.

    An SDK that predates the fields refuses them as unknown members; one
    with another floor would disagree with this assembler's rule.
    """
    source = (Path(sdk) / 'pkg/aiiospkg/runtime.go').read_text()
    if re.findall(r'^const RuntimeExtentMinHost = "([^"]*)"$', source, re.M) != [RUNTIME_EXTENT_MIN_HOST]:
        raise ValueError('authoring SDK does not read runtime extents from host ' + RUNTIME_EXTENT_MIN_HOST
                         + '; use one that declares RuntimeExtentMinHost (aii-plugin-sdk f8b4961 or later)')


def accelerator_domains(variant):
    """GPU domains named by a set's backend token ('cpu', 'metal', 'cuda+vulkan', ...)."""
    backend = variant['accelerator'].get('backend')
    tokens = backend.split('+') if isinstance(backend, str) and backend else []
    measurable = MEASURED_DOMAINS.get(variant['platform'], {})
    if (not tokens or len(set(tokens)) != len(tokens)
            or any(t != 'cpu' and t not in measurable for t in tokens)):
        raise ValueError('backend names no measurable accelerator domain on this desktop: '
                         + variant['variant_id'])
    return {t: measurable[t] for t in tokens if t != 'cpu'}


def reservation_contract(variant, measured):
    """A GPU reservation is a measured claim, never a zero that skips admission.

    Device memory needs independently measured justification: the set's
    hash-bound evidence must measure its complete composition on every domain
    it requires, and each reservation must cover that observed peak. The
    reservation stays the release owner's budget; assembly never derives it.
    """
    profile, name = variant['accelerator'], variant['variant_id']
    domains = accelerator_domains(variant)
    device = profile['device_memory_bytes']
    if set(profile.get('required_accelerators', [])) != set(domains):
        raise ValueError('set must require exactly the accelerator domains its backend uses: ' + name)
    if not domains:
        return  # a positive device reservation without a domain is refused above
    kinds = set(domains.values())
    if kinds == {'unified'}:
        if device != 0:
            raise ValueError('unified accelerator footprint belongs in memory_bytes: ' + name)
    elif kinds != {'discrete'} or device <= 0:
        raise ValueError('discrete GPU set needs a positive measured device reservation: ' + name)
    evidence = (measured or {}).get(name)
    rows = evidence.get('domains') if isinstance(evidence, dict) else None
    if (not isinstance(rows, dict) or evidence.get('schema') != RESERVATION_EVIDENCE
            or evidence.get('passed') is not True or evidence.get('complete_composition') is not True
            or evidence.get('variant_id') != name
            or not re.fullmatch('[0-9a-f]{64}', str(evidence.get('runtime_manifest_sha256')))
            or set(rows) != set(domains)):
        raise ValueError('GPU reservation needs independently measured complete-composition evidence: ' + name)
    for domain, kind in domains.items():
        peak = rows[domain].get('measured_peak_bytes') if isinstance(rows[domain], dict) else None
        if type(peak) is not int or peak <= 0:
            raise ValueError('measured peak missing: ' + name + '/' + domain)
        if (profile['memory_bytes'] if kind == 'unified' else device) < peak:
            raise ValueError('reservation is below its measured peak: ' + name + '/' + domain)


def measured_reservations(bindings, bound):
    """Evidence must have measured the exact staged runtime of the set it justifies."""
    measured = {}
    for variant, row in bindings.items():
        evidence = row.get('measured_reservation')
        if evidence is None:
            continue
        if (not isinstance(evidence, dict) or evidence.get('variant_id') != variant
                or evidence.get('runtime_manifest_sha256') != bound[variant]['runtime_manifest_sha256']):
            raise ValueError('reservation evidence measured a different composition: ' + variant)
        measured[variant] = evidence
    return measured


def authoring_template(root, digest):
    """Read explicit, byte-bound author metadata; never a private old release."""
    root = Path(root).resolve()
    receipt = root/'authoring-inputs.json'
    if sha(receipt) != digest:
        raise ValueError('authoring input receipt changed')
    record = json.loads(receipt.read_text())
    if set(record) != {'schema', 'files'} or record['schema'] != 'aiii.voice.authoring-inputs.v1':
        raise ValueError('authoring input receipt fields differ')
    rows = record['files']
    mandatory = {'plugin.json', 'release-notices.json', 'notices/INDEX.json'}
    if not isinstance(rows, dict) or not mandatory <= rows.keys() or len(rows) > 4096:
        raise ValueError('complete bounded authoring input inventory required')
    for name, row in rows.items():
        safe_relative(name)
        if name not in mandatory and not name.startswith('notices/'):
            raise ValueError('authoring inputs contain undeclared payload or data')
        path = root/name
        if (set(row) != {'sha256', 'size'} or type(row['size']) is not int
                or not 0 <= row['size'] <= 64*1024*1024
                or path.is_symlink() or not path.is_file()
                or not path.resolve().is_relative_to(root)
                or path.stat().st_size != row['size'] or sha(path) != row['sha256']):
            raise ValueError('authoring input bytes differ: '+name)
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if not p.is_dir()}
    if actual != set(rows) | {'authoring-inputs.json'}:
        raise ValueError('authoring input file census differs')
    notices = json.loads((root/'release-notices.json').read_text())
    if (not isinstance(notices, list)
            or len({r['path'] for r in notices}) != len(notices)
            or {r['path']: {k:r[k] for k in ('sha256','size')} for r in notices}
               != {n:r for n,r in rows.items() if n.startswith('notices/')}):
        raise ValueError('notice inventory differs from authoring inputs')
    return json.loads((root/'plugin.json').read_text()), json.loads((root/'notices/INDEX.json').read_text()), notices


def candidate_inputs(path):
    """Select explicit immutable staging, never guess the latest directory."""
    if path is None:
        raise ValueError('explicit candidate input manifest required; historical stages are not release defaults')
    def unique_object(pairs):
        row = {}
        for key, value in pairs:
            if key in row:
                raise ValueError('duplicate candidate input key: ' + key)
            row[key] = value
        return row
    declaration = json.loads(path.read_text(), object_pairs_hook=unique_object)
    if not isinstance(declaration, dict) or set(declaration) != {'variants', 'variant_preference'}:
        raise ValueError('explicit variants and variant_preference required')
    rows, order = declaration['variants'], declaration['variant_preference']
    if (not isinstance(rows, dict) or not 1 <= len(rows) <= 64
            or not isinstance(order, list) or any(not isinstance(v, str) for v in order)
            or len(order) != len(rows) or len(set(order)) != len(order) or set(order) != set(rows)):
        raise ValueError('variant_preference must name each bound variant exactly once')
    bindings = {}
    for variant in order:
        if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}', variant):
            raise ValueError('invalid bound variant ID')
        row = rows[variant]
        if not isinstance(row, dict) or not isinstance(row.get('accelerator'), dict):
            raise ValueError('explicit accelerator declaration required: ' + variant)
        if row.get('platform') not in PLATFORMS or row.get('arch') not in ('x86_64', 'arm64'):
            raise ValueError('explicit desktop coordinates required: ' + variant)
        binding = copy.deepcopy(row)
        binding['stage'] = Path(row['stage']).resolve()
        binding['carrier'] = Path(row['carrier']).resolve()
        if sha(binding['stage'] / 'result.json') != row['stage_sha256']:
            raise ValueError('staging result changed: ' + variant)
        if sha(binding['carrier']) != row['carrier_sha256']:
            raise ValueError('carrier changed: ' + variant)
        evidence = row.get('reservation_evidence')
        if evidence is not None:
            if not isinstance(evidence, dict) or set(evidence) != {'path', 'sha256'}:
                raise ValueError('reservation evidence binding needs exactly path and sha256: ' + variant)
            path = Path(evidence['path']).resolve()
            if sha(path) != evidence['sha256']:
                raise ValueError('reservation evidence changed: ' + variant)
            binding['measured_reservation'] = json.loads(path.read_text(), object_pairs_hook=unique_object)
        bindings[variant] = binding
    if {r['platform'] for r in bindings.values()} != PLATFORMS:
        raise ValueError('all three desktop bindings required')
    return bindings, list(order)


def bind_variants(cfg, bindings, order):
    """Expand platform metadata, never invent a set's bytes or resources."""
    templates = {v['platform']: v for v in cfg['variants']}
    if set(templates) != PLATFORMS or len(cfg['variants']) != len(PLATFORMS):
        raise ValueError('one metadata template per desktop required')
    variants = []
    for variant in order:
        row = bindings[variant]
        template = templates[row['platform']]
        if row['arch'] != template['arch']:
            raise ValueError('bound architecture differs from desktop template: ' + variant)
        v = copy.deepcopy(template)
        v.update(variant_id=variant, accelerator=copy.deepcopy(row['accelerator']))
        variants.append(v)
    cfg['variants'] = variants
    cfg['variant_preference'] = list(order)
    cfg.pop('default_variant', None)


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


def release_contract(cfg, minimum_host_version, measured=None):
    """Apply agreed metadata without changing models, budgets or setting values.

    `measured` maps a GPU set's variant ID to its stage-bound reservation
    evidence (see measured_reservations); CPU sets need none.
    """
    if host_version(minimum_host_version) < host_version(COMPONENT_SELECTION_MIN_HOST):
        raise ValueError('minimum host ' + minimum_host_version + ' predates the documented '
                         'component-selection floor ' + COMPONENT_SELECTION_MIN_HOST)
    keys = {'stt_language', 'turn_pause_ms', 'capture_limit_minutes', 'vad_threshold',
            'tts_voice', 'tts_language', 'tts_temperature', 'tts_seed'}
    variants = cfg['variants']
    names = [v['variant_id'] for v in variants]
    if ({v['platform'] for v in variants} != PLATFORMS or not 3 <= len(variants) <= 64
            or len(set(names)) != len(names)):
        raise ValueError('unique variants covering every desktop required')
    preference = cfg.get('variant_preference')
    if (not isinstance(preference, list) or len(preference) != len(names)
            or any(not isinstance(v, str) for v in preference)
            or len(set(preference)) != len(names) or set(preference) != set(names)):
        raise ValueError('complete explicit variant_preference required')
    if {s['key'] for s in cfg['settings']} != keys or len(cfg['settings']) != len(keys):
        raise ValueError('review scope for changed settings')
    if any(s.get('scope') not in ('hearing', 'speaking', 'session') for s in cfg['settings']):
        raise ValueError('compiled setting scope required; packaging does not invent it')
    for v in cfg['variants']:
        profile = v['accelerator']
        if type(profile.get('memory_bytes')) is not int or profile['memory_bytes'] <= 0:
            raise ValueError('existing positive host reservation required')
        if type(profile.get('device_memory_bytes')) is not int or profile['device_memory_bytes'] < 0:
            raise ValueError('explicit nonnegative device reservation required')
        required = profile.get('required_accelerators', [])
        if (not isinstance(required, list) or any(not isinstance(r, str) or not r for r in required)
                or len(set(required)) != len(required)
                or (profile['device_memory_bytes'] > 0 and not required)):
            raise ValueError('device reservation requires explicit accelerator domains')
        if (profile.get('os'), profile.get('arch')) != (v['platform'], v['arch']):
            raise ValueError('accelerator coordinates differ from variant')
        reservation_contract(v, measured)
        if type(profile.get('startup_ms')) is not int or not 1 <= profile['startup_ms'] <= 3600000:
            raise ValueError('explicit bounded startup_ms required')
    # The release owner supplies the host version carrying THIS candidate's
    # requirements. Never reset a newer requirement to a historical constant.
    # The SDK packager remains the authority for version syntax/admission.
    cfg['aiios_min_version'] = minimum_host_version


def readiness_inside_startup(variants, profiles):
    """Each set's wait for its worker's readiness ends inside the start it declares.

    A set declares startup_ms to the host as the allowance for its start
    (release_contract requires it), and its runtime's profile states
    ready_ms, how long its carrier waits for its worker to report ready
    before it says, with the number, that it did not. A carrier that waited
    as long as the allowance would not have said so when the allowance
    passed. The carrier is not told what its set declares, so a set that
    pairs the two wrongly is refused here, with both numbers and its name.

    This is called from main, the one place where both are in hand:
    release_contract is given the declarations and no profile, and runtime
    reads a stage's profile before any declaration is bound to it.
    """
    for v in variants:
        variant = v['variant_id']
        try:
            startup_covers_readiness(profile_limits(profiles[variant], released=True),
                                     v['accelerator']['startup_ms'])
        except ValueError as refusal:
            raise ValueError(str(refusal) + ': ' + variant) from None


def image_census(result, profile, variant):
    """The stage receipt must show a default-deny scan of every shipped image.

    Receipts from the earlier allow-list scanner covered only recognized
    names and must be re-staged, not edited to look complete.
    """
    rows = result.get('native_image_privacy')
    if not isinstance(rows, list) or not rows:
        raise ValueError('re-stage this runtime to bind its complete native image census: ' + variant)
    carrier = 'aii-voice-t3.exe' if profile['platform'] == 'windows' else 'aii-voice-t3'
    files, census = profile['files'], {}
    for row in rows:
        name = row.get('file') if isinstance(row, dict) else None
        if (name in census or row.get('role') not in ('release_owned', 'third_party')
                or not isinstance(row.get('findings'), dict)):
            raise ValueError('native image census row is invalid: ' + variant)
        census[name] = row
    images = {n for n, r in files.items() if r.get('executable') or IMAGE_NAME.search(n)} | {carrier}
    if not images <= set(census) or not set(census) <= set(files) | {carrier}:
        raise ValueError('native image census differs from the runtime inventory: ' + variant)
    for name, row in census.items():
        expected = result['carrier_sha256'] if name == carrier else files[name]['sha256']
        if row.get('sha256') != expected:
            raise ValueError('native image census names different bytes: ' + variant + '/' + name)
        if any(row['role'] == 'release_owned' or key.split(':', 1)[-1] in ALWAYS_FAIL
               for key in row['findings']):
            raise ValueError('staged image carries private strings: ' + variant + '/' + name)
        if row['role'] == 'third_party' and (name == carrier or release_owned_image(name)):
            raise ValueError('release-built image declared third-party: ' + variant + '/' + name)
    return census


def windows_authenticode(bound, profiles):
    """Recompute the claim from per-image observations; a bare boolean is not evidence.

    It holds only when every release-built PE image and the carrier of every
    Windows set were verified. Third-party images keep vendor signatures.
    """
    for variant, profile in profiles_on(profiles, 'windows').items():
        images = bound[variant].get('authenticode_images')
        needed = [n for n in (*profile['files'], 'aii-voice-t3.exe')
                  if n.lower().endswith(('.dll', '.exe')) and release_owned_image(n)]
        if not isinstance(images, dict) or any(images.get(n) != 'verified_publisher' for n in needed):
            return False
    return True


def profiles_on(profiles, platform):
    return {variant: profile for variant, profile in profiles.items()
            if {'darwin': 'macos'}.get(profile['platform'], profile['platform']) == platform}


def shared_descriptors(bound):
    descriptors = None
    for variant, result in bound.items():
        actual = result.get('descriptors')
        if not isinstance(actual, list) or not actual:
            raise ValueError('re-stage this carrier to bind its callable contract: ' + variant)
        if descriptors is not None and actual != descriptors:
            raise ValueError('component-set callable contracts disagree: ' + variant)
        descriptors = actual
    return descriptors


def native_contract_carrier(bindings, *, system=None, machine=None):
    """Choose an executable for this build machine, not a fixed desktop.

    Describe does not load models. A cross-platform family still needs one
    native readback of the callable contract; foreign carriers stay byte-bound.
    """
    host_os = {'Darwin': 'macos', 'Linux': 'linux', 'Windows': 'windows'}.get(
        platform.system() if system is None else system)
    host_arch = {'arm64': 'arm64', 'aarch64': 'arm64',
                 'x86_64': 'x86_64', 'amd64': 'x86_64'}.get(
        (platform.machine() if machine is None else machine).lower())
    for row in bindings.values():
        if host_os is not None and host_arch is not None and (
                row['platform'], row['arch']) == (host_os, host_arch):
            return row['carrier']
    raise ValueError('no bound carrier can be executed natively on this assembly machine')


def verify_packaged_compositions(manifest, files, cfg, carriers):
    """Prove the SDK did not collapse sets sharing an operating system."""
    variants = {v['variant_id']: v for v in manifest['variants']}
    if len(variants) != len(cfg['variants']) or set(variants) != set(carriers):
        raise ValueError('packaged variant census differs')
    if manifest.get('variant_preference') != cfg['variant_preference']:
        raise ValueError('packaged selection preference differs')
    if json.loads(files['models.json']) != cfg['models']:
        raise ValueError('packaged model union differs')
    if json.loads(files['settings.json']) != cfg['settings']:
        raise ValueError('packaged settings differ')
    if json.loads(files['runtime.json'])['runtimes'] != cfg['runtimes']:
        raise ValueError('packaged runtime bindings differ')
    accelerators = json.loads(files['accelerator.json'])
    if set(accelerators) != set(variants):
        raise ValueError('packaged accelerator census differs')
    for declared in cfg['variants']:
        variant = declared['variant_id']
        v = variants[variant]
        if (v['platform'], v['arch']) != (declared['platform'], declared['arch']):
            raise ValueError('packaged coordinates differ: ' + variant)
        if files[v['entrypoint']] != carriers[variant].read_bytes():
            raise ValueError('packaged carrier differs: ' + variant)
        for key, value in declared['accelerator'].items():
            # The SDK omits optional empty lists; they have no selected member.
            if value != [] and accelerators[variant].get(key) != value:
                raise ValueError('packaged accelerator binding differs: ' + variant + '.' + key)


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
    # A stage whose profile describes an interpreter is not assembled: the
    # carrier would refuse to start it, and no package carries one.
    try:refuse_interpreter_profile(profile)
    except ValueError as refusal:raise ValueError(str(refusal)+': '+str(stage)) from None
    # A package states the time limits each of its runtimes ships with. A
    # stage from before staging required them is not assembled: its
    # checkpoint is rebuilt so that its profile states them, and staged again.
    try:profile_limits(profile,released=True)
    except ValueError as refusal:raise ValueError(str(refusal)+': '+str(stage)) from None
    rows={**profile['files'],'voice-runtime.json':dict(bytes=len(raw),sha256=result['runtime_manifest_sha256'],executable=False)}
    check_archive(archive,result['runtime_archive'],rows,windows=profile['platform']=='windows')
    archive_extent(result['runtime_archive'],rows)
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
    moved = sorted(k for k in set(before) | set(after) | set(record.get('models', {}))
                   if separator_model_path(k) and (before.get(k) != after.get(k) or k in record.get('models', {})))
    if moved:
        raise ValueError('separator weights need their own upstream terms, not the NVIDIA hearing record: '
                         + ', '.join(moved))
    if ({k:v for k,v in before.items() if not recognition_model_path(k)} !=
            {k:v for k,v in after.items() if not recognition_model_path(k)}):
        raise ValueError('hearing replacement changes another component')
    hearing = {k:dict(sha256=v['sha256'], bytes=v['size']) for k,v in after.items() if recognition_model_path(k)}
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
        files['notices/'+HEARING_NOTICE_GROUP+'/'+name] = raw
    files['notices/'+HEARING_NOTICE_GROUP+'/HEARING-REPLACEMENT.json'] = record_path.read_bytes()
    updated = copy.deepcopy(index)
    updated['models'] = [r for r in updated['models'] if not recognition_model_path(r['path'])]
    updated['models'].extend(dict(path=name, **row, notice_group=HEARING_NOTICE_GROUP) for name,row in hearing.items())
    updated['hearing_replacement'] = record
    # A prior model's legal disposition cannot qualify these different weights.
    updated['distribution_review_complete'] = False
    updated['open_items'].append(HEARING_REVIEW_ITEM)
    cfg['models'] = copy.deepcopy(model['models'])
    index.clear(); index.update(updated)
    return files


def recognition_model_path(path):
    """NVIDIA-derived recognition data in the Full/Small hearing namespaces.

    This is the voice package's component layout, not a host/SDK rule. A
    hearing review must bind both sets without widening into UID or TTS, or
    into separator weights that share the directory but not the upstream.
    """
    return path.startswith(('stt/', 'stt-small/')) and not separator_model_path(path)


def apply_hearing_disposition(index, cfg, notices, source, digest):
    """Resolve only this export's review; never inherit unrelated clearance."""
    if sha(source) != digest:
        raise ValueError('hearing disposition changed')
    review = json.loads(source.read_text())
    record = index.get('hearing_replacement', {})
    if any(separator_model_path(path) for path in (*record.get('models', {}), *review.get('models', {}))):
        raise ValueError('hearing review cannot clear separator weights')
    models = {m['path']: dict(sha256=m['sha256'], bytes=m['size'])
              for m in cfg['models'] if recognition_model_path(m['path'])}
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


def notice_coverage(index, profiles, census, notices):
    """Bind third-party notices and shipped binaries in both directions.

    Each library row names exact shipped bytes on every composition it covers
    and an included notice group. Each shipped native image is release-built
    (covered by the release's own notices) or bound by such a row; an image
    that is neither is not presumed ours, whatever its scan found.
    """
    libraries = index.get('libraries')
    if not isinstance(libraries, list):
        raise ValueError('third-party library notice inventory required')
    included = {n['path'] for n in notices if n.get('size')}
    covered = {}
    for lib in libraries:
        group = lib.get('notice_group')
        if (not isinstance(group, str) or not group or group != group.strip() or group.startswith('/')
                or '..' in group.split('/') or '\\' in group
                or not any(path.startswith('notices/' + group + '/') for path in included)):
            raise ValueError('third-party library names no included notice: ' + str(lib.get('component')))
        eligible = profiles_on(profiles, lib['platform'])
        names = lib.get('variants', list(eligible))
        if not names or any(v not in eligible for v in names):
            raise ValueError('third-party notice names an unbound composition')
        for variant in names:
            hashes = {f['sha256'] for f in eligible[variant]['files'].values()}
            if lib['shipped_sha256'] not in hashes:
                raise ValueError('third-party notice binding changed: '+variant+'/'+lib['component'])
            covered.setdefault(variant, set()).add(lib['shipped_sha256'])
    for variant, rows in census.items():
        for name, row in rows.items():
            if name in ('aii-voice-t3', 'aii-voice-t3.exe') or (
                    row['role'] == 'release_owned' and release_owned_image(name)):
                continue
            if row['sha256'] not in covered.get(variant, set()):
                raise ValueError('shipped image has no bound third-party notice: ' + variant + '/' + name)


def separator_attribution(index, cfg, notices):
    """Separator weights carry their own pinned upstream terms, never NVIDIA's.

    The exporter pins the MossFormer2 source revision and checkpoint digest;
    this repository records neither its upstream repository nor its license,
    so the release owner supplies both with the notice texts they name.
    """
    separators = {m['path']: dict(sha256=m['sha256'], bytes=m['size'])
                  for m in cfg['models'] if separator_model_path(m['path'])}
    if not separators:
        return None
    record = index.get('separator_provenance')
    if (not isinstance(record, dict)
            or record.get('source_revision') != SEPARATOR_UPSTREAM_REVISION
            or record.get('checkpoint_sha256') != SEPARATOR_CHECKPOINT_SHA256
            or record.get('models') != separators
            or not all(isinstance(record.get(k), str) and record[k].strip()
                       for k in ('repository', 'license', 'notice_group'))):
        raise ValueError('separator weights need pinned upstream provenance and terms')
    group = record['notice_group']
    if (group == HEARING_NOTICE_GROUP
            or not any(n['path'].startswith('notices/' + group + '/') and n.get('size') for n in notices)):
        raise ValueError('separator weights need their own included notice group')
    rows = {r['path']: r for r in index['models']}
    for path, binding in separators.items():
        row = rows.get(path, {})
        if (row.get('notice_group') != group or row.get('sha256') != binding['sha256']
                or row.get('bytes') != binding['bytes']):
            raise ValueError('separator model is not bound to its own notice group: ' + path)
    return dict(record)


def apply_distribution_disposition(index, cfg, profiles, notices, source, digest):
    """Carry a named prior decision only over its unchanged components/notices.

    This does not inherit a prior package signature, installation or release
    status. Reproducibility limitations stay visible, not reopened as licensing
    blockers merely because another runtime image was rebuilt.
    """
    if sha(source) != digest:
        raise ValueError('distribution disposition changed')
    prior=json.loads(source.read_text())
    if HEARING_REVIEW_ITEM in index['open_items'] or HEARING_REVIEW_ITEM in prior.get('resolved_items', []):
        # Only apply_hearing_disposition, bound to the exact exports and
        # notices, may close it; a generic addendum names no hearing bytes.
        raise ValueError('hearing review needs its own bound disposition, not a generic addendum')
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
            actual=[p['files'].get(row['component'].removeprefix('windows/'),{})
                    for p in profiles_on(profiles, 'windows').values()]
        else:
            actual=[models.get(row['component'],{})]
        if (not actual or any(r.get('sha256') != row['sha256'] for r in actual)
                or row['notice_sha256'] not in notice_hashes):
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
    p.add_argument('--inputs',type=Path,required=True,help='Variant-keyed stage/carrier SHA-256 bindings, accelerator declarations and complete variant_preference')
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
    p.add_argument('--minimum-host-version',required=True,
                   help='Explicit released host version carrying the qualified requirements; no historical default')
    p.add_argument('--author-template',type=Path,required=True,
                   help='Explicit current metadata and notices, with authoring-inputs.json; no historical default')
    p.add_argument('--author-template-sha256',required=True,
                   help='Exact authoring-inputs.json binding')
    p.add_argument('--go-modcache',type=Path,required=True)
    p.add_argument('--go',type=Path,required=True,help='Qualified local Go toolchain for the authoring SDK')
    p.add_argument('--authoring-sdk',type=Path,required=True)
    p.add_argument('--authoring-sdk-archive',type=Path,required=True)
    p.add_argument('--authoring-sdk-archive-sha256',required=True)
    a=p.parse_args();out=a.out.resolve();pin,_=verify_sdk()
    template=a.author_template.resolve()
    bindings, preference = candidate_inputs(a.inputs)
    carriers = {v: row['carrier'] for v, row in bindings.items()}
    authoring_sdk = a.authoring_sdk.resolve()
    sdk_archive = a.authoring_sdk_archive.resolve()
    sdk_inventory = verify_sdk_source(authoring_sdk, sdk_archive, a.authoring_sdk_archive_sha256)
    authoring_extent_floor(authoring_sdk)
    cfg,index,notice_rows=authoring_template(template,a.author_template_sha256)
    # Notice selection and model selection form one atomic packaging decision.
    uid_files={}
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
        old_base=RELEASE_DOWNLOAD+cfg['version']+'/'
        new_base=RELEASE_DOWNLOAD+a.version+'/'
        for model in cfg['models']:
            if model['url'].startswith(old_base):model['url']=new_base+model['url'][len(old_base):]
        cfg['version']=a.version
    # Signature/readiness status belongs in evidence, not descriptive metadata
    # that would remain falsely "unsigned" after the exact package is signed.
    cfg['title']='AII Voice'
    cfg['description']='On-device speech for macOS, Windows and Ubuntu: English recognition that separates speakers, with a correction list the identity can teach; twenty selectable voices in seven speaking languages; adjustable VAD, interruption/recovery and durable anonymous speaker UUIDs with later naming. Speaker attribution is a model estimate, not authentication or authority.'
    cfg['runtimes']=[]
    bound={};profiles={};census={}
    settings = None
    for variant, row in bindings.items():
        bound[variant], profiles[variant] = runtime(row['stage'])
        coordinates = composition_coordinates(profiles[variant], variant)
        if (bound[variant]['variant_id'] != variant
                or coordinates != (row['platform'], row['arch'], variant)
                or bound[variant]['sdk_revision'] != pin['revision']):
            raise ValueError('runtime composition coordinates differ: ' + variant)
        if sha(carriers[variant]) != bound[variant]['carrier_sha256']:
            raise ValueError('carrier changed: ' + variant)
        census[variant] = image_census(bound[variant], profiles[variant], variant)
        declared = runtime_settings(bound[variant], profiles[variant])
        if settings is not None and declared != settings:
            raise ValueError('desktop runtime settings declarations disagree')
        settings = declared
    cfg['settings'] = settings
    bind_variants(cfg, bindings, preference)
    release_contract(cfg, a.minimum_host_version, measured_reservations(bindings, bound))
    readiness_inside_startup(cfg['variants'], profiles)  # only here are a set's profile and its checked declaration both in hand
    # Each declaration carries its stage's extent; refuse before any output.
    declarations={v['variant_id']:runtime_declaration(bound[v['variant_id']]['runtime_archive'],v['variant_id'],cfg['version'])
                  for v in cfg['variants']}
    runtime_extent([decl for _,decl in declarations.values()],cfg['aiios_min_version'])
    descriptors = shared_descriptors(bound)
    # Read back the native carrier too; no foreign binary is executed here.
    native = native_contract_carrier(bindings)
    if descriptors != json.loads(subprocess.check_output([str(native)],
            env={'PATH':'','AIISDK_DESCRIBE':'1'},timeout=10)):
        raise ValueError('staged callable contract differs from native carrier')
    cfg['interfaces']=enrollment_interfaces(descriptors)
    schemas={d[k] for d in descriptors for k in ('input','output') if d.get(k)}
    expected_schemas={'schemas/speaker-'+name+'.input.json' for name in
        ('list','enroll','remove','reset','discard_capture','upgrade_policy','buckets','associate','link','forget')}
    expected_schemas.update(('schemas/speaker.output.json','schemas/speaker-buckets.output.json'))
    expected_schemas.update(('schemas/recording-record.input.json',
                             'schemas/recording-status.input.json',
                             'schemas/recording-list.input.json',
                             'schemas/recording-delete.input.json',
                             'schemas/recording-prune.input.json',
                             'schemas/recording.output.json',
                             'schemas/recording-list.output.json',
                             'schemas/recording-delete.output.json',
                             'schemas/recording-prune.output.json'))
    expected_schemas.update(('schemas/vocabulary-list.input.json',
                             'schemas/vocabulary-correct.input.json',
                             'schemas/vocabulary-forget.input.json',
                             'schemas/vocabulary.output.json'))
    if schemas!=expected_schemas:raise ValueError('complete speaker schema set required')
    out.mkdir(parents=True,exist_ok=False);author=out/'author'
    assets={};plans={}
    for v in cfg['variants']:
        platform=v['platform'];variant=v['variant_id'];r=bound[variant]
        if variant!=r['variant_id']:raise ValueError('variant binding differs')
        v['artifact']='payloads/'+variant
        copy_asset(carriers[variant],author/v['artifact'],carriers[variant].stat().st_size,r['carrier_sha256'])
        arc=r['runtime_archive'];name,decl=declarations[variant]
        copy_asset(arc['path'],out/'assets'/name,arc['size'],arc['sha256'])
        cfg['runtimes'].append(decl);assets[name]=dict(kind='runtime',variant_id=variant,sha256=arc['sha256'],size=arc['size'])
        # Do not change measured model/backend/resource choices with packaging.
        selected=selected_models(cfg['models'],v['accelerator'],r)
        if ('endpoint/windows/coefficients.f32' in {m['path'] for m in selected})!=(platform=='windows'):
            raise ValueError('foreign platform endpoint model selected')
        plans[variant]=dict(platform=platform,arch=v['arch'],variant_id=variant,
            accelerator=copy.deepcopy(v['accelerator']),runtime=decl,carrier_sha256=r['carrier_sha256'],
            carrier_path=str((author/v['artifact']).resolve()),models=selected,
            runtime_archive_path=str((out/'assets'/name).resolve()),
            operator_config_merge=operator_setup(platform))
    # These are setup instructions using existing host keys, not a new SDK
    # declaration and not permission to replace a whole identity config.
    emit(out/'operator-setup.json',dict(
        scope='Operator-reviewed merge into existing host config; never replace config.json. No plugin self-authorization.',
        restart_required=False,
        platforms={p:operator_setup(p) for p in sorted(PLATFORMS)},
        rationale='AII OS consumes each selected set\'s explicit startup allowance subject to operator ceilings and overrides. Host/device memory are supplied reservations, not measured peaks or invented by assembly; every GPU reservation covers its stage-bound measured complete-composition peak.',
        automatic_configuration=False))
    # Notices are original texts with exact attribution. Rebind the one stale
    # execution description; do not represent notice collection as clearance.
    if uid_files:notice_rows=[r for r in notice_rows if not r['path'].startswith('notices/wespeaker-uid/')]
    for row in notice_rows:
        if row['path']!='notices/INDEX.json':
            copy_asset(template/row['path'],author/row['path'],row['size'],row['sha256'])
    # Current vendor provenance is supplied and byte-bound with the template,
    # not reinterpreted as one historical ORT/OpenMP distribution. The checks
    # below bind notices and shipped images in both directions.
    files=dict(uid_files)
    files.update(hearing_files)
    notice_rows=[r for r in notice_rows if r['path']!='notices/INDEX.json']
    for name,raw in files.items():
        put(author/name,raw)
        notice_rows.append(dict(path=name,sha256=hashlib.sha256(raw).hexdigest(),size=len(raw)))
    index['original_notice_files']=copy.deepcopy(notice_rows)
    notice_coverage(index, profiles, census, notice_rows)
    separator_attribution(index, cfg, notice_rows)
    index['release_notice_packaging']=dict(notices='included_and_byte_bound',
        declared_urls='pinned_upstream_and_new_release_destinations',
        public_release_url_availability='not_asserted_by_assembly')
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
    for label,cmd in [('build',[str(a.go.resolve()),'build','-trimpath','-buildvcs=false','-o',str(assembler),str(ROOT/'scripts/private_cp1_package.go')]),
                      ('assemble',[str(assembler),str(author)])]:
        r=subprocess.run(cmd,cwd=authoring_sdk,env=env,capture_output=True,timeout=120)
        put(out/(label+'.stdout'),r.stdout);put(out/(label+'.stderr'),r.stderr)
        if r.returncode:raise RuntimeError(label+' failed; retained output')
    assembly=json.loads(r.stdout);manifest,files=read_package(author/assembly['bundle'],assembly['sha256'])
    verify_packaged_compositions(manifest, files, cfg, carriers)
    metadata_privacy = package_metadata(manifest, files)
    if verify_sdk_source(authoring_sdk, sdk_archive, a.authoring_sdk_archive_sha256) != sdk_inventory:
        raise ValueError('authoring SDK changed during assembly')
    authoring_template(template,a.author_template_sha256)
    for name in schemas:
        if files[name]!=(ROOT/'plugin/native'/name).read_bytes():raise ValueError('schema not packed exactly')
    emit(out/'variant-plans.json',plans);emit(out/'release-assets.json',assets)
    emit(out/'result.json',dict(passed=True,scope=__doc__,bundle=assembly,sdk_revision=pin['revision'],
        source_sha256=sha(__file__),assembler_sha256=sha(ROOT/'scripts/private_cp1_package.go'),
        bound_staging={v:sha(row['stage']/'result.json') for v,row in bindings.items()},variants=list(plans),
        variant_preference=preference,authoring_sdk_archive_sha256=a.authoring_sdk_archive_sha256,
        authoring_inputs_sha256=a.author_template_sha256,
        explicit_inputs_sha256=sha(a.inputs) if a.inputs else None,
        reservation_evidence_sha256={v: row['reservation_evidence']['sha256'] for v, row in bindings.items()
                                     if row.get('reservation_evidence') is not None},
        speaker_methods=[d['id'] for d in descriptors if d['id'].startswith('speaker.')],
        input_output_schema_files=sorted(schemas),models=len(cfg['models']),notices=len(notice_rows),
        signed=False,installed=False,published=False,beta_release_ready=False,
        release_status=dict(package_integrity='verified',package_signature='not_performed_by_assembly',
            installed_journey='not_performed_by_assembly',technical_acceptance='platform_audits_only',
            distribution_review='complete' if index['distribution_review_complete'] else 'open',publication='not_performed_by_assembly'),
        package_metadata_privacy=metadata_privacy,
        third_party_images={v: sorted(n for n, r in c.items() if r['role'] == 'third_party') for v, c in census.items()},
        windows_authenticode_verified=windows_authenticode(bound, profiles),
        windows_authenticode_images={v: bound[v].get('authenticode_images') for v in profiles_on(profiles, 'windows')},
        required_before_release=([] if windows_authenticode(bound, profiles)
            else ['Authenticode and rebind Windows runtime/carrier'])+[
          'host guided capture, UID ingress filters and AI-visible policy',
          'final signed fresh-cache installed journeys','authorized T3 signature and host verification',
          'signed catalog and hosted byte readback']+([] if index['distribution_review_complete'] else ['third-party distribution review'])))
    print(json.dumps({'bundle':assembly,'variants':list(plans),'signed':False,'published':False}))


if __name__=='__main__':main()
