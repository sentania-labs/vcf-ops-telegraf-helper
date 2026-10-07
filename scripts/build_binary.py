"""Shared local, PR and release PyInstaller build definition."""
import argparse
from importlib.metadata import version
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile


def mac_icon() -> Path:
    """Render the product's vector icon at native Retina icon sizes."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    icon = Path('build/vcf-telegraf-helper.icns').resolve()
    icon.parent.mkdir(exist_ok=True)
    renderer = QSvgRenderer('vcf_ops_telegraf_helper/gui/assets/app.svg')
    with tempfile.TemporaryDirectory() as directory:
        iconset = Path(directory) / 'helper.iconset'
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                image = QImage(size * scale, size * scale, QImage.Format_ARGB32)
                image.fill(Qt.transparent)
                painter = QPainter(image)
                renderer.render(painter)
                painter.end()
                suffix = '@2x' if scale == 2 else ''
                if not image.save(str(iconset / f'icon_{size}x{size}{suffix}.png')):
                    raise RuntimeError('Could not render app icon')
        subprocess.run(['iconutil', '-c', 'icns', '-o', str(icon), str(iconset)], check=True)
    return icon


def build(platform: str) -> None:
    mac = platform.startswith('macos-')
    name = 'vcf-telegraf-helper' if mac else f'vcf-telegraf-helper-{platform}'
    options = ['--onedir', '--windowed', '--osx-bundle-identifier',
               'net.sentania.vcf-telegraf-helper'] if mac else ['--onefile']
    if mac:
        options += ['--icon', str(mac_icon())]
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', *options, '--clean', '--noconfirm',
        '--name', name,
        '--copy-metadata', 'vcf-ops-telegraf-helper',
        '--collect-all', 'vcf_ops_telegraf_helper', '--collect-all', 'tzdata',
        '--collect-all', 'winrm', '--collect-all', 'truststore',
        '--hidden-import', 'PySide6.QtCore', '--hidden-import', 'PySide6.QtGui',
        '--hidden-import', 'PySide6.QtWidgets', 'vcf_ops_telegraf_helper/cli/main.py',
    ], check=True)
    if mac:
        app = Path('dist/VCF Telegraf Helper.app')
        # Refuse to replace a previous app, which may already be signed.
        Path(f'dist/{name}.app').rename(app)
        plist_path = app / 'Contents/Info.plist'
        with plist_path.open('rb') as stream:
            plist = plistlib.load(stream)
        full_version = version('vcf-ops-telegraf-helper')
        bundle_version = re.match(r'\d+\.\d+\.\d+', full_version).group()
        plist.update(VCFHelperVersion=full_version, CFBundleName='VCF Telegraf Helper',
                     CFBundleDisplayName='VCF Telegraf Helper',
                     CFBundleShortVersionString=bundle_version,
                     CFBundleVersion=bundle_version,
                     NSHighResolutionCapable=True)
        with plist_path.open('wb') as stream:
            plistlib.dump(plist, stream)
        # Refresh PyInstaller's ad-hoc seal after metadata changes. Release CI
        # replaces it with Developer ID signatures on all nested code.
        subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('platform', choices=['linux', 'windows', 'macos-arm64', 'macos-x86_64'])
    build(parser.parse_args().platform)
