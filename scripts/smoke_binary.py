"""Run the packaged CLI and Qt window outside the checkout."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

binary = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix='telegraf-smoke-') as directory:
    copy = Path(shutil.copy2(binary, directory))
    copy.chmod(copy.stat().st_mode | 0o111)
    env = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}
    for args, expected in [(['--help'], 'Usage:'), (['render', '--cpu', '--mem'], 'inputs.cpu'),
                           (['gui-smoke'], 'GUI smoke passed')]:
        result = subprocess.run([str(copy), *args], cwd=directory, env=env,
                                capture_output=True, text=True, timeout=120, check=True)
        if expected not in result.stdout:
            raise RuntimeError(f'{args} failed: {result.stdout} {result.stderr}')
        print(f'PASS: {args}')
