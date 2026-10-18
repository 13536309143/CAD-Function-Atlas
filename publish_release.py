"""Explicit publishing commands using existing local logins; never prints credential values."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile

import httpx


ROOT = Path(__file__).resolve().parent
REPOSITORY = '13536309143/llm'
VERSION = 'v0.1.1'


def github_headers():
    result = subprocess.run(['git', 'credential', 'fill'], input='protocol=https\nhost=github.com\n\n',
                            capture_output=True, text=True, timeout=30,
                            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never'})
    fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if not fields.get('password'):
        raise RuntimeError('No existing GitHub credential available; log in locally')
    return {'Authorization': 'Bearer ' + fields['password'], 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28'}


def release_archives(dataset):
    from package_dataset import secrets, clean_text
    known = secrets()
    out = ROOT / 'dist' / 'releases';out.mkdir(parents=True, exist_ok=True)
    files = subprocess.run(['git', 'ls-files', '-z'], capture_output=True, check=True).stdout.decode('utf-8').split('\0')
    source_zip = out / f'cad-function-atlas-{VERSION}.zip'
    with zipfile.ZipFile(source_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name in files:
            if name and (ROOT / name).is_file():
                if name == '.env' or name.startswith(('data/', 'outputs/', '.venv/')):
                    raise ValueError('Private/generated path unexpectedly tracked by Git')
                path = ROOT / name
                if path.suffix.lower() in {'.py', '.md', '.json', '.csv', '.txt', '.ps1', '.yml', '.ini', '.example'}:
                    clean_text(path.read_text(encoding='utf-8-sig'), known)
                z.write(ROOT / name, f'cad-function-atlas-{VERSION}/' + name)
    # The final source asset comes from the exact committed tree, including LF normalization.
    subprocess.run(['git', 'archive', '--format=zip', '--prefix=cad-function-atlas-' + VERSION + '/',
                    '--output=' + str(source_zip), 'HEAD'], check=True)
    snapshot_zip = out / f'benchmark-snapshot-{VERSION}.zip'
    with zipfile.ZipFile(snapshot_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name in ['records', 'benchmark', 'reports']:
            for path in sorted((dataset / name).rglob('*')):
                if path.is_file():
                    z.write(path, path.relative_to(dataset).as_posix())
        z.write(dataset / 'README.md', 'README.md')
    sums = []
    for path in [source_zip, snapshot_zip]:
        with path.open('rb') as f:
            sums.append(hashlib.file_digest(f, 'sha256').hexdigest() + '  ' + path.name)
    (out / 'SHA256SUMS').write_text('\n'.join(sums) + '\n', encoding='utf-8')
    print(json.dumps({'release_assets': [{'name': p.name, 'bytes': p.stat().st_size} for p in out.iterdir()]}, indent=2))


def publish_hf(dataset):
    from huggingface_hub import HfApi
    api = HfApi();user = api.whoami()['name']
    if user != 'GeorgeWJJ':
        raise ValueError('Authenticated HF account changed; refusing to publish to a different namespace')
    repo = user + '/cad-function-atlas'
    api.create_repo(repo, repo_type='dataset', private=False, exist_ok=True)
    commit = api.upload_folder(repo_id=repo, repo_type='dataset', folder_path=dataset,
                               commit_message='Publish CAD Function Atlas v0.1.0 frozen partial benchmark',
                               ignore_patterns=['.cache/**'])
    receipt = {'repo': repo, 'url': 'https://huggingface.co/datasets/' + repo, 'commit': str(commit), 'private': False}
    (ROOT / 'outputs' / 'publication' / 'huggingface_receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps(receipt, indent=2))


def publish_github_release():
    headers = github_headers();api = 'https://api.github.com/repos/' + REPOSITORY
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
    body = (ROOT / 'docs' / 'RELEASE_NOTES.md').read_text(encoding='utf-8')
    with httpx.Client(headers=headers, timeout=120, follow_redirects=False) as client:
        profile = client.get('https://api.github.com/user');profile.raise_for_status()
        if profile.json().get('login') != '13536309143':
            raise ValueError('GitHub account mismatch')
        existing = client.get(api + '/releases/tags/' + VERSION)
        if existing.status_code == 404:
            response = client.post(api + '/releases', json={'tag_name': VERSION, 'target_commitish': head,
                'name': f'CAD Function Atlas {VERSION} — Industrial CAD Function Benchmark', 'body': body,
                'draft': False, 'prerelease': True})
            response.raise_for_status();release = response.json()
        else:
            existing.raise_for_status();release = existing.json()
        upload = release['upload_url'].split('{', 1)[0]
        if not upload.startswith('https://uploads.github.com/'):
            raise ValueError('Unexpected release-upload host')
        names = {x['name'] for x in release.get('assets', [])}
        expected_names = {f'cad-function-atlas-{VERSION}.zip', f'benchmark-snapshot-{VERSION}.zip', 'SHA256SUMS'}
        for path in sorted((ROOT / 'dist' / 'releases').iterdir()):
            if not path.is_file() or path.name in names or path.name not in expected_names:
                continue
            with path.open('rb') as stream:
                result = client.post(upload, params={'name': path.name}, content=stream,
                                     headers={'Content-Type': 'application/octet-stream', 'Content-Length': str(path.stat().st_size)})
            result.raise_for_status()
        result = client.patch(api, json={'description': 'CAD Function Atlas: multimodal functional classification and reproducible evaluation for industrial CAD meshes.',
                                         'homepage': 'https://huggingface.co/datasets/GeorgeWJJ/cad-function-atlas'})
        result.raise_for_status()
        result = client.put(api + '/topics', json={'names': ['cad', 'multimodal', 'functional-classification', 'benchmark', 'minimax', 'openai', 'industrial-ai']})
        result.raise_for_status()
    receipt = {'release_url': release['html_url'], 'commit': head, 'tag': VERSION, 'prerelease': True}
    (ROOT / 'outputs' / 'publication' / 'github_release_receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['archives', 'huggingface', 'github-release'])
    p.add_argument('--dataset', type=Path, default=ROOT / 'dist' / 'huggingface')
    a = p.parse_args()
    if a.command == 'archives':
        release_archives(a.dataset)
    elif a.command == 'huggingface':
        publish_hf(a.dataset)
    else:
        publish_github_release()
