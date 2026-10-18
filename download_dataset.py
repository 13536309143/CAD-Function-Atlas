"""Download the released dataset; optional checksum-verified, path-safe extraction."""
import argparse
import hashlib
from pathlib import Path
import stat
import zipfile


def extract_safely(archive, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(destination):
                raise ValueError('Archive path escapes extraction directory')
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError('Archive contains a symlink')
        z.extractall(destination)


def verify(folder):
    folder = Path(folder).resolve()
    for line in (folder / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        expected, name = line.split('  ', 1)
        path = (folder / name).resolve()
        if not path.is_relative_to(folder):
            raise ValueError('Checksum path escapes dataset directory')
        with path.open('rb') as f:
            actual = hashlib.file_digest(f, 'sha256').hexdigest()
        if actual != expected:
            raise ValueError('Checksum mismatch: ' + name)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', default='GeorgeWJJ/cad-function-atlas')
    p.add_argument('--out', type=Path, default=Path('downloaded_dataset'))
    p.add_argument('--extract', action='store_true')
    a = p.parse_args()
    from huggingface_hub import snapshot_download
    snapshot_download(a.repo, repo_type='dataset', local_dir=a.out)
    verify(a.out)
    if a.extract:
        for folder in ['meshes', 'renders', 'audit']:
            for archive in sorted((a.out / folder).glob('*.zip')):
                extract_safely(archive, a.out)
    print('Dataset downloaded and checksum-verified.' + (' Archives extracted.' if a.extract else ''))
