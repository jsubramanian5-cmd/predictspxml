"""Freeze the installed transitive closure for the tested Windows/Python environment.

XGBoost is pinned from its documented compatible release when unavailable locally;
its only Windows runtime requirements are NumPy and SciPy. See VALIDATION.md.
"""
from collections import deque
from importlib.metadata import distribution, PackageNotFoundError
from pathlib import Path
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

roots = ['numpy', 'pandas', 'pyarrow', 'scikit-learn', 'xgboost', 'matplotlib',
         'requests', 'jupyterlab', 'nbformat', 'nbclient', 'nbconvert', 'ipykernel',
         'tzdata', 'lxml', 'pytest', 'setuptools', 'scipy', 'arrow', 'types-python-dateutil']
queue = deque((name, frozenset()) for name in roots)
seen, locked = set(), {}
unavailable_pins = {'xgboost': '3.1.3', 'fqdn': '1.5.1', 'uri-template': '1.3.0',
                    'isoduration': '20.11.0', 'webcolors': '24.11.1',
                    'types-python-dateutil': '2.9.0.20241206'}
while queue:
    name, extras = queue.popleft()
    name = canonicalize_name(name)
    key = name, extras
    if key in seen:
        continue
    seen.add(key)
    try:
        dist = distribution(name)
    except PackageNotFoundError:
        if name in unavailable_pins:
            locked[name] = unavailable_pins[name]
            continue
        raise
    locked[name] = dist.version
    for raw in dist.requires or []:
        req = Requirement(raw)
        if req.marker and not any(req.marker.evaluate({'extra': extra}) for extra in (set(extras) | {''})):
            continue
        try:
            installed = distribution(req.name).version
            if installed not in req.specifier:
                raise RuntimeError(f'Incompatible installed dependency: {raw}, installed {installed}')
        except PackageNotFoundError:
            canonical = canonicalize_name(req.name)
            if canonical not in unavailable_pins:
                raise RuntimeError(f'Missing transitive dependency: {raw}')
            print(f'Unverified local install: {canonical}=={unavailable_pins[canonical]}')
        queue.append((req.name, frozenset(req.extras)))
header = '# Exact transitive pins for Windows x64, Python 3.13.9.\n'
header += '# Generated from installed metadata plus uninstalled XGBoost/jsonschema-format pins.\n'
header += '# Missing locally: xgboost, fqdn, isoduration, uri-template, webcolors, types-python-dateutil.\n'
header += '# See VALIDATION.md. Other OS/Python combinations require re-resolution.\n'
Path('requirements.lock').write_text(header + '\n'.join(f'{name}=={locked[name]}' for name in sorted(locked)) + '\n')
print(f'Locked {len(locked)} distributions for {sys.version.split()[0]}')
