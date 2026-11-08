import importlib.metadata as metadata
import sys

print(sys.version)
for package in ['numpy', 'pandas', 'pyarrow', 'scikit-learn', 'xgboost',
                'matplotlib', 'jupyterlab', 'nbformat', 'nbclient', 'nbconvert',
                'requests', 'pytest', 'ipykernel']:
    try:
        print(f'{package}=={metadata.version(package)}')
    except metadata.PackageNotFoundError:
        print(f'{package}: MISSING')
