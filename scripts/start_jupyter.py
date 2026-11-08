"""Launch JupyterLab with data files discoverable in an inherited Anaconda setup."""
import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def launch_environment(root=ROOT):
    environment = os.environ.copy()
    own_data = Path(sys.prefix)/'share/jupyter'
    base_data = Path(sys.base_prefix)/'share/jupyter'
    own_app, base_app = own_data/'lab', base_data/'lab'
    if 'JUPYTERLAB_DIR' not in environment and not (own_app/'static/index.html').is_file():
        if (base_app/'static/index.html').is_file():
            environment['JUPYTERLAB_DIR'] = str(base_app)
    if base_data != own_data and base_data.is_dir():
        current_paths = environment.get('JUPYTER_PATH', '').split(os.pathsep)
        environment['JUPYTER_PATH'] = os.pathsep.join(
            dict.fromkeys([str(base_data)] + [path for path in current_paths if path]))
    runtime = root/'.runtime'
    for variable, directory in [('JUPYTER_RUNTIME_DIR', runtime/'jupyter'),
                                ('IPYTHONDIR', runtime/'ipython')]:
        if variable not in environment:
            directory.mkdir(parents=True, exist_ok=True)
            environment[variable] = str(directory)
    return environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check app files without starting a server')
    args, extra = parser.parse_known_args()
    environment = launch_environment()
    application = Path(environment.get('JUPYTERLAB_DIR', str(Path(sys.prefix)/'share/jupyter/lab')))
    if not (application/'static/index.html').is_file():
        parser.error(f'JupyterLab web files not found in {application}; install JupyterLab into this environment')
    print(f'Python: {sys.executable}', flush=True)
    print(f'JupyterLab web files: {application}', flush=True)
    if args.check:
        print('JupyterLab application files found; server not started.')
        return
    command = [sys.executable, '-m', 'jupyterlab', str(ROOT/'notebooks/spx_forecast.ipynb'),
               f'--ServerApp.root_dir={ROOT}', *extra]
    log_path = ROOT/'.runtime/jupyter-launch.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f'Launch/error log: {log_path}', flush=True)
    with log_path.open('a', encoding='utf-8') as log:
        log.write(f'\n--- Jupyter launch {datetime.now().isoformat()} ---\n')
        log.flush()
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        try:
            for line in process.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                # Keep the normal browser login link in the user's terminal, but
                # omit auth credentials from the diagnostic file we may inspect.
                log.write(re.sub(r'(token=)[^&\s\'"<>]+', r'\1[REDACTED]', line, flags=re.IGNORECASE))
                log.flush()
            return_code = process.wait()
        except KeyboardInterrupt:
            try:
                return_code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                return_code = process.wait(timeout=5)
    raise SystemExit(return_code)


if __name__ == '__main__':
    main()
