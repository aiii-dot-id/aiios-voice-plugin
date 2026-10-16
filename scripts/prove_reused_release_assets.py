"""Bind already-public content-addressed model assets without uploading duplicates.

The unauthenticated GitHub release API reports the complete asset size and
SHA-256 digest. This is metadata evidence, not a fresh body download; the host
still hashes the complete body during every installation.
"""
from scripts._assertions import require_assertions
require_assertions()
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen

from scripts.stage_desktop_publication import REPOSITORY, clean_url


def reuse_rows(models, evidence, version):
    if evidence.get('passed') is not True or evidence.get('method') != 'anonymous_github_release_asset_digest':
        raise ValueError('public release digest evidence required')
    rows = evidence.get('rows')
    if not isinstance(rows, list):
        raise ValueError('reused asset rows required')
    by_path = {}
    for row in rows:
        if not isinstance(row, dict) or row.get('path') in by_path:
            raise ValueError('duplicate or invalid reused asset')
        by_path[row['path']] = row
    current = '/'+REPOSITORY+'/releases/download/v'+version+'/'
    result = []
    for model in models:
        url = clean_url(model['url'])
        if url.hostname != 'github.com' or url.path.startswith(current):
            continue
        matched = re.fullmatch(r'/'+re.escape(REPOSITORY)+r'/releases/download/(v[0-9A-Za-z.-]+)/([A-Za-z0-9_.-]+)', url.path)
        if not matched or not matched[2].startswith(model['sha256']+'-') or matched[1]=='v'+version:
            raise ValueError('unowned or unbound reused model URL')
        row = by_path.get(model['path'], {})
        expected = dict(path=model['path'], url=model['url'], sha256=model['sha256'],
                        size=model['size'], release_tag=matched[1], asset_name=matched[2],
                        repository=REPOSITORY, github_asset_digest='sha256:'+model['sha256'],
                        github_asset_size=model['size'])
        if any(row.get(key) != value for key, value in expected.items()):
            raise ValueError('reused asset evidence differs from signed declaration: '+model['path'])
        result.append(expected)
    if set(by_path) != {row['path'] for row in result}:
        raise ValueError('reused asset evidence census differs')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    models = json.loads(args.models.read_text())
    tags = set()
    for model in models:
        path = clean_url(model['url']).path
        matched = re.fullmatch(r'/'+re.escape(REPOSITORY)+r'/releases/download/(v[0-9A-Za-z.-]+)/([A-Za-z0-9_.-]+)', path)
        if matched and matched[1] != 'v'+args.version:
            tags.add(matched[1])
    releases = {}
    for tag in sorted(tags):
        request = Request('https://api.github.com/repos/'+REPOSITORY+'/releases/tags/'+tag,
                          headers={'Accept':'application/vnd.github+json','User-Agent':'aiii-voice-publication-check'})
        with urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise ValueError('public release API refused '+tag)
            release = json.load(response)
        if release.get('draft') or release.get('tag_name') != tag:
            raise ValueError('reused release is not public and exact')
        releases[tag] = {asset['name']:asset for asset in release['assets']}
    rows = []
    for model in models:
        url = clean_url(model['url'])
        match = re.fullmatch(r'/'+re.escape(REPOSITORY)+r'/releases/download/(v[0-9A-Za-z.-]+)/([A-Za-z0-9_.-]+)', url.path)
        if not match or match[1] == 'v'+args.version:
            continue
        asset = releases.get(match[1], {}).get(match[2], {})
        if asset.get('size') != model['size'] or asset.get('digest') != 'sha256:'+model['sha256']:
            raise ValueError('public asset size or digest differs: '+model['path'])
        rows.append(dict(path=model['path'],url=model['url'],sha256=model['sha256'],size=model['size'],
                         release_tag=match[1],asset_name=match[2],repository=REPOSITORY,
                         github_asset_digest=asset['digest'],github_asset_size=asset['size']))
    evidence=dict(passed=True,method='anonymous_github_release_asset_digest',
                  complete_body_hashed=False,host_install_must_hash_body=True,
                  utc=datetime.now(timezone.utc).isoformat(),rows=rows)
    reuse_rows(models,evidence,args.version)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    if args.out.exists():
        raise ValueError('refusing to overwrite release evidence')
    args.out.write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps(dict(passed=True,reused_assets=len(rows),body_hashed=False)))


if __name__=='__main__':
    main()
