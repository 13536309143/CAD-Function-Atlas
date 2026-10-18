"""Offline environment diagnostics; API access requires an explicit --live flag."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import tempfile


def diagnose(out, render=False, live=False):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    result = {'python': platform.python_version(), 'platform': platform.platform(), 'checks': {}, 'live_api_tested': False}
    for name in ('numpy', 'trimesh', 'pyvista', 'vtk', 'pillow', 'httpx', 'scipy', 'networkx', 'matplotlib', 'python-dotenv'):
        try:
            result['checks'][name] = {'ok': True, 'version': importlib.metadata.version(name)}
        except importlib.metadata.PackageNotFoundError:
            result['checks'][name] = {'ok': False, 'error': 'Not installed'}
    try:
        import trimesh
        from cad_geometry import prepare_mesh, render_views
        path = out / 'diagnostic_cube.stl'
        trimesh.creation.box(extents=[1, 2, 3]).export(path)
        mesh, _, geometry = prepare_mesh(path)
        result['checks']['geometry'] = {'ok': geometry['triangles'] > 0, 'triangles': geometry['triangles']}
        if render or live:
            image = render_views(mesh, out / 'views', tile=160)
            result['checks']['render'] = {'ok': image.exists(), 'image': str(image)}
        if live:
            from cad_agents import create_vision_client
            from run_pilot import capability_probe
            client = create_vision_client(max_calls=1)
            capability_probe(client, out)
            result['live_api_tested'] = True
            result['checks']['vision_capability'] = {'ok': True, 'provider': getattr(client, 'provider', 'minimax')}
    except Exception as exc:
        result['checks']['pipeline'] = {'ok': False, 'error_type': type(exc).__name__, 'error': str(exc)[:500]}
    result['ok'] = all(x['ok'] for x in result['checks'].values())
    (out / 'environment.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('outputs/environment_check'))
    parser.add_argument('--render', action='store_true')
    parser.add_argument('--live', action='store_true', help='Explicitly spend one vision request on an image-only capability probe')
    args = parser.parse_args()
    report = diagnose(args.out, args.render, args.live)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    sys.exit(0 if report['ok'] else 1)
