"""Mesh checks and offscreen multi-view rendering. No semantic labels in payloads."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
import trimesh

VIEWS = {
    'front': ((0, -1, 0), (0, 0, 1)),
    'back': ((0, 1, 0), (0, 0, 1)),
    'left': ((-1, 0, 0), (0, 0, 1)),
    'right': ((1, 0, 0), (0, 0, 1)),
    'top': ((0, 0, 1), (0, 1, 0)),
    'bottom': ((0, 0, -1), (0, 1, 0)),
    'iso_a': ((1, -1, 1), (0, 0, 1)),
    'iso_b': ((-1, 1, -1), (0, 0, 1)),
}
EXTRA_VIEWS = {
    'oblique_a': ((1, 1, .45), (0, 0, 1)),
    'oblique_b': ((-1, -1, .45), (0, 0, 1)),
    'oblique_c': ((.45, 1, -1), (0, 1, 0)),
    'oblique_d': ((1, .45, 1), (0, 0, 1)),
}


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def prepare_mesh(path: Path, augmentation=None):
    if path.suffix.lower() not in {'.obj', '.stl', '.glb'}:
        raise ValueError('Supported mesh formats: OBJ, STL, GLB')
    # force=scene preserves node transforms; OBJ material names are never sent to API.
    scene = trimesh.load_scene(path, process=False)
    nodes = list(scene.graph.nodes_geometry)
    if not nodes:
        raise ValueError('No triangle geometry')
    parts = []
    invalid_vertices = 0
    removed_faces = 0
    duplicate_faces = 0
    for node in nodes:
        transform, geometry_name = scene.graph[node]
        part = scene.geometry[geometry_name].copy()
        if not isinstance(part, trimesh.Trimesh):
            continue
        part.apply_transform(transform)
        if augmentation is not None:
            part.apply_transform(augmentation)
        finite = np.isfinite(part.vertices).all(axis=1)
        invalid_vertices += int((~finite).sum())
        good_faces = finite[part.faces].all(axis=1)
        removed_faces += int((~good_faces).sum())
        part.update_faces(good_faces)
        part.remove_unreferenced_vertices()
        # Geometry topology must not treat UV or sharp-normal seams as cracks.
        part.merge_vertices(merge_tex=True, merge_norm=True)
        good_faces = part.nondegenerate_faces()
        removed_faces += int((~good_faces).sum())
        part.update_faces(good_faces)
        unique = part.unique_faces()
        duplicate_faces += int((~unique).sum())
        part.update_faces(unique)
        part.remove_unreferenced_vertices()
        if len(part.faces):
            parts.append(part)
    if not parts:
        raise ValueError('No valid triangles after cleaning')
    mesh = trimesh.util.concatenate(parts)
    bounds = mesh.bounds.copy()
    extents = mesh.extents.copy()
    scale = float(extents.max())
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError('Degenerate model bounding box')
    components = list(mesh.split(only_watertight=False))
    watertight = bool(mesh.is_watertight)
    consistent = bool(mesh.is_winding_consistent)
    volume_valid = bool(mesh.is_volume)
    volume = float(abs(mesh.volume)) if volume_valid else None
    area = float(mesh.area)
    sorted_dims = np.sort(extents)
    warnings = []
    if not volume_valid:
        warnings.append('Volume unavailable: mesh is open, has inconsistent winding or is not a valid solid.')
    if len(nodes) > 1:
        warnings.append('Multiple scene nodes: classification targets the complete assembly; node meshes exported separately.')
    if invalid_vertices or removed_faces:
        warnings.append('Invalid geometry removed; inspect cleaning counts.')
    if duplicate_faces:
        warnings.append('Duplicate coincident faces removed before topology/volume computation; geometry silhouette preserved.')
    if float(sorted_dims[0]) <= scale * 1e-7:
        warnings.append('Near-planar model; thickness-dependent classification requires review.')
    features = {
        'coordinate_units': 'unknown; do not infer millimetres',
        'bounds_source_units': bounds.tolist(),
        'extents_source_units': extents.tolist(),
        'extent_ratios_max_normalized': (extents / scale).tolist(),
        'sorted_extent_ratios': (sorted_dims / scale).tolist(),
        'area_source_units_squared': area,
        'volume_source_units_cubed': volume,
        'normalized_area': area / scale**2,
        'normalized_volume': volume / scale**3 if volume is not None else None,
        'bbox_solid_fill_fraction': volume / float(np.prod(extents)) if volume is not None and np.prod(extents) > 0 else None,
        'vertices': len(mesh.vertices), 'triangles': len(mesh.faces),
        'watertight': watertight, 'winding_consistent': consistent,
        'euler_number': int(mesh.euler_number),
        'connected_components': len(components), 'scene_nodes': len(nodes),
        'invalid_vertices_removed': invalid_vertices,
        'invalid_or_degenerate_faces_removed': removed_faces,
        'duplicate_faces_removed': duplicate_faces,
        'warnings': warnings,
        'topology_note': 'Euler number is a mesh property, not a direct count of machined holes. Disconnected shells need not be separate parts.',
    }
    # Use bbox centre rather than centre of mass, which is unreliable for open meshes.
    centre = bounds.mean(axis=0)
    mesh.vertices = (mesh.vertices - centre) / scale
    for part in parts:
        part.vertices = (part.vertices - centre) / scale
    return mesh, parts, features


def render_views(mesh, out: Path, extra=False, tile=480, definitions=None,
                 background='#f2f5f8', color='#9cb8ca'):
    import pyvista as pv

    out.mkdir(parents=True, exist_ok=True)
    definitions = definitions if definitions is not None else (EXTRA_VIEWS if extra else VIEWS)
    faces = np.column_stack((np.full(len(mesh.faces), 3), mesh.faces)).ravel()
    surface = pv.PolyData(mesh.vertices, faces)
    paths = []
    for name, (direction, up) in definitions.items():
        plotter = pv.Plotter(off_screen=True, window_size=(tile, tile))
        try:
            plotter.set_background(background)
            plotter.add_mesh(surface, color=color, smooth_shading=False,
                             ambient=.35, diffuse=.65, specular=.1,
                             show_edges=False, backface_culling=False)
            direction = np.array(direction, dtype=float)
            direction /= np.linalg.norm(direction)
            plotter.camera_position = [tuple(direction * 3), (0, 0, 0), up]
            plotter.enable_parallel_projection()
            plotter.camera.parallel_scale = .91
            plotter.add_text(name, position='upper_left', font_size=12, color='#233248')
            target = out / f'{name}.png'
            plotter.screenshot(str(target))
            paths.append(target)
        finally:
            plotter.close()
    columns = 2 if extra else 4
    rows = (len(paths) + columns - 1) // columns
    sheet = Image.new('RGB', (tile * columns, tile * rows), background)
    for index, path in enumerate(paths):
        with Image.open(path) as picture:
            sheet.paste(picture.convert('RGB'), (index % columns * tile, index // columns * tile))
    target = out / ('additional_views.jpg' if extra else 'views.jpg')
    sheet.save(target, quality=94)
    # Fail before calling the API if rendering produced empty images.
    pixels = np.asarray(sheet)
    if float(pixels.std()) < 3:
        raise ValueError('Render looks empty')
    return target


def prepare_sample(path: Path, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    mesh, parts, features = prepare_mesh(path)
    (out / 'geometry.json').write_text(json.dumps(features, ensure_ascii=False, indent=2), encoding='utf-8')
    if len(parts) > 1:
        nodes = out / 'nodes'
        nodes.mkdir(exist_ok=True)
        for index, part in enumerate(parts):
            part.export(nodes / f'node_{index:03d}.glb')
    image = render_views(mesh, out)
    return mesh, features, image
