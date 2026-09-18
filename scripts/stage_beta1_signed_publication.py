"""Seal a locally verified signed release handoff; never upload or install it."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
from scripts.repackage_native_schemas import read_package
from scripts.stage_desktop_publication import dependencies,upstream_rows,platform_plan,clean_url,release_name,REPOSITORY
from scripts.prepare_desktop_distribution import copy_asset,emit,put,sha
from scripts.verify_publication_catalog import check_catalog


def handoff_status(index):
    """State this command's evidence boundary, not a hard-coded beta verdict.

    Installed journeys and release acceptance are established by their own
    artifact-bound gates. Staging cannot call them failed, passed, or stale.
    """
    complete = index['distribution_review_complete']
    items = index['open_items']
    if type(complete) is not bool or not isinstance(items, list) or any(not isinstance(x, str) for x in items):
        raise ValueError('invalid distribution review status')
    if complete and items:
        raise ValueError('complete distribution review has open items')
    return dict(
        artifact_integrity='verified', package_signature='T3_verified',
        distribution_review='complete' if complete else 'open',
        technical_acceptance='not_assessed_by_staging',
        publication='not_published_by_staging',
        installation='not_performed_by_staging')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('candidate','signed','host-verification','generated','upstream','out','sdk-tool'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();out=a.out.resolve();candidate=a.candidate.resolve();signed=a.signed.resolve()
    verdict=json.loads(a.host_verification.read_text());h=sha(signed)
    if not (verdict['passed'] and verdict['tier']=='T3' and verdict['signed_package_sha256']==h
            and verdict['tampered_archive_rejected'] and verdict['host_vcs_modified'] is False):
        raise ValueError('exact host crypto/tamper verdict required')
    receipt=json.loads((candidate/'result.json').read_text());built=receipt['bundle']
    old,oldfiles=read_package(candidate/'author'/built['bundle'],built['sha256'])
    manifest,files=read_package(signed,h,platform_signature=True)
    if old!=manifest or oldfiles!=files:raise ValueError('signing altered package members')
    models,runtimes,targets=dependencies(manifest,files)
    upstream=json.loads(a.upstream.read_text());external=upstream_rows(models,upstream)
    prepared=[]
    for kind,rows in (('model',models),('runtime',runtimes)):
        for row in rows:
            if clean_url(row['url']).hostname!='github.com':
                if kind!='model':raise ValueError('unowned runtime download')
                continue
            name=release_name(row,manifest['version'])
            src=(a.generated if kind=='model' else candidate/'assets')/name
            if src.is_symlink() or not src.is_file() or sha(src)!=row['sha256'] or src.stat().st_size!=row['size']:
                raise ValueError('missing exact release asset: '+name)
            if row['size']>=2*1024**3:raise ValueError('oversized GitHub asset')
            prepared.append(dict(kind=kind,file='assets/'+name,size=row['size'],sha256=row['sha256'],url=row['url'],source=str(src)))
    url=f'https://github.com/{REPOSITORY}/releases/download/v{manifest["version"]}/{signed.name}'
    prepared.append(dict(kind='plugin',file='assets/'+signed.name,size=signed.stat().st_size,sha256=h,url=url,source=str(signed)))
    if len({r['file'] for r in prepared})!=len(prepared):raise ValueError('asset collision')
    out.mkdir(parents=True,exist_ok=False)
    for row in prepared:copy_asset(row['source'],out/row['file'],row['size'],row['sha256'])
    rows=[{k:v for k,v in r.items() if k!='source'} for r in prepared]
    setup=json.loads((candidate/'operator-setup.json').read_text())
    sdk=a.sdk_tool.resolve();sdk_sha=sha(sdk)
    cmd=[str(sdk),'publish','-tier','T3','-pkg',str(signed),'-url',url]
    r=subprocess.run(cmd,cwd=candidate/'author',capture_output=True,timeout=45)
    put(out/'catalog.stdout',r.stdout);put(out/'catalog.stderr',r.stderr)
    if r.returncode:raise ValueError('SDK refused signed catalog input')
    if sha(sdk)!=sdk_sha:raise ValueError('SDK publisher changed during catalog generation')
    catalog=json.loads(r.stdout);check_catalog(catalog,manifest,h,signed.stat().st_size,url)
    emit(out/'catalog-entry.json',catalog)
    index=json.loads(files['notices/INDEX.json'])
    status=handoff_status(index)
    plan=dict(schema='aiii-voice-signed-desktop-publication-handoff',utc=datetime.now(timezone.utc).isoformat(),
        repository=REPOSITORY,release_tag='v'+manifest['version'],signed_package_sha256=h,
        assets=rows,upstream=external,platforms=platform_plan(targets,setup),
        host_verification_sha256=sha(a.host_verification),catalog_entry_sha256=sha(out/'catalog-entry.json'),sdk_tool_sha256=sdk_sha,
        upstream_evidence_sha256=sha(a.upstream),distribution_review_complete=index['distribution_review_complete'],
        distribution_open_items=index['open_items'],signed=True,tier='T3',catalog_entry_generated=True,
        published=False,installed=False,release_status=status,
        acceptance_contract='docs/BETA1_VOICE_RELEASE.md')
    emit(out/'publication-plan.json',plan);put(out/'operator-setup.json',(candidate/'operator-setup.json').read_bytes())
    put(out/'host-verification.json',a.host_verification.read_bytes());put(out/'upstream-download-evidence.json',a.upstream.read_bytes())
    put(out/'SHA256SUMS',''.join(r['sha256']+'  '+r['file']+'\n' for r in sorted(rows,key=lambda r:r['file'])).encode())
    for r in rows:
        if sha(out/r['file'])!=r['sha256'] or (out/r['file']).stat().st_size!=r['size']:raise ValueError('handoff bytes changed')
    emit(out/'result.json',dict(passed=True,source_sha256=sha(__file__),signed_package_sha256=h,
        assets=len(rows),upstream_files=len(external),platforms=sorted(targets),distinct_plugin_archives=1,
        total_asset_bytes=sum(r['size'] for r in rows),tier='T3',signed=True,catalog_entry_generated=True,
        published=False,installed=False,release_status=status))
    print(json.dumps(dict(passed=True,assets=len(rows),signed_package_sha256=h,catalog_entry_generated=True)))


if __name__=='__main__':main()
