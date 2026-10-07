"""Run packaged CLI and Qt outside the checkout, including macOS app launches."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def smoke(artifact: Path, signed: bool = False) -> None:
    with tempfile.TemporaryDirectory(prefix='telegraf-smoke-') as directory:
        root = Path(directory)
        if artifact.suffix == '.zip':
            subprocess.run(['ditto', '-x', '-k', str(artifact), str(root)], check=True)
            app = root / 'VCF Telegraf Helper.app'
        elif artifact.suffix == '.app':
            app = root / artifact.name
            shutil.copytree(artifact, app, symlinks=True)
        else:
            app = None
        if app:
            binary = app / 'Contents/MacOS/vcf-telegraf-helper'
            if signed:
                subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
                subprocess.run(['xcrun', 'stapler', 'validate', str(app)], check=True)
                subprocess.run(['spctl', '--assess', '--type', 'execute', str(app)], check=True)
        else:
            binary = Path(shutil.copy2(artifact, root))
            binary.chmod(binary.stat().st_mode | 0o111)
        env = dict(os.environ)
        if app:
            # Exercise native Cocoa, without the offscreen plugin hiding launch problems.
            for key in ('QT_QPA_PLATFORM', 'DISPLAY', 'WAYLAND_DISPLAY', 'VCF_HELPER_NO_GUI'):
                env.pop(key, None)
        else:
            env['QT_QPA_PLATFORM'] = 'offscreen'
        for args, expected in [(['--help'], 'Usage:'), (['render', '--cpu', '--mem'], 'inputs.cpu'),
                               (['gui-smoke'], 'GUI smoke passed')]:
            result = subprocess.run([str(binary), *args], cwd=root, env=env,
                                    capture_output=True, text=True, timeout=120)
            if result.returncode or expected not in result.stdout:
                raise RuntimeError(f'{args} failed ({result.returncode}): {result.stdout} {result.stderr}')
            print(f'PASS: {args}', flush=True)
        if app:
            # Match the documented symlink and LaunchServices/Finder entry points.
            link = root / 'vcf-telegraf-helper'
            link.symlink_to(binary)
            subprocess.run([str(link), 'render', '--cpu'], cwd=root, env=env,
                           check=True, timeout=120, stdout=subprocess.PIPE)
            report = root / 'finder-launch.png'
            subprocess.run(['open', '-n', '-W', '--env',
                            f'VCF_HELPER_GUI_SMOKE_REPORT={report}', str(app)],
                           cwd=root, env=env, check=True, timeout=120)
            if not report.is_file() or report.stat().st_size < 100:
                raise RuntimeError('Finder launch did not render the GUI')
            # Keep the image for inspection in the workflow artifact.
            evidence = Path('dist/finder-launch.png')
            evidence.parent.mkdir(exist_ok=True)
            shutil.copy2(report, evidence)
            print('PASS: Finder launch rendered the native GUI; bundled CLI and symlink work', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('artifact', type=Path)
    parser.add_argument('--signed', action='store_true')
    args = parser.parse_args()
    if args.artifact.suffix in ('.app', '.zip') and sys.platform != 'darwin':
        parser.error('macOS app smoke tests require macOS')
    smoke(args.artifact.resolve(), args.signed)
