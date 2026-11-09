"""Shared local, PR and release PyInstaller build definition."""
import argparse
from importlib.metadata import version
from pathlib import Path
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile

from scripts.generate_notices import (
    collect_bundled_distributions,
    generate_notices_text,
    verify_notices,
)


def generate_and_verify_notices() -> Path:
    """Generate and verify THIRD-PARTY-NOTICES.txt before freezing the binary."""
    dists = collect_bundled_distributions()
    text = generate_notices_text(dists)
    verify_notices(text, dists)

    notices_file = Path("vcf_ops_telegraf_helper/THIRD-PARTY-NOTICES.txt")
    notices_file.write_text(text, encoding="utf-8")
    Path("THIRD-PARTY-NOTICES.txt").write_text(text, encoding="utf-8")
    dist_dir = Path("dist")
    dist_dir.mkdir(exist_ok=True)
    (dist_dir / "THIRD-PARTY-NOTICES.txt").write_text(text, encoding="utf-8")
    return notices_file


def package_dmg(app_path: Path, dmg_path: Path, volume_name: str = "VCF Telegraf Helper") -> Path:
    """Package a .app bundle into a drag-to-Applications .dmg."""
    dmg_path.parent.mkdir(parents=True, exist_ok=True)
    if dmg_path.exists():
        dmg_path.unlink()
    with tempfile.TemporaryDirectory(prefix="vcf-dmg-") as staging:
        staging_dir = Path(staging)
        shutil.copytree(app_path, staging_dir / app_path.name, symlinks=True)
        (staging_dir / "Applications").symlink_to("/Applications")
        subprocess.run([
            "hdiutil", "create",
            "-volname", volume_name,
            "-srcfolder", str(staging_dir),
            "-ov",
            "-format", "UDZO",
            str(dmg_path),
        ], check=True)
    return dmg_path


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


def windows_icon() -> Path:
    """Encode all Windows icon sizes as PNG entries in one ICO container."""
    from PySide6.QtCore import Qt, QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer('vcf_ops_telegraf_helper/gui/assets/app.svg')
    sizes = (16, 32, 48, 64, 128, 256)
    images = []
    for size in sizes:
        image = QImage(size, size, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        if not image.save(buffer, 'PNG'):
            raise RuntimeError('Could not render Windows icon')
        images.append(bytes(data))
    offset = 6 + 16 * len(sizes)
    entries = []
    for size, data in zip(sizes, images):
        entries.append(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    icon = Path('build/vcf-telegraf-helper.ico').resolve()
    icon.parent.mkdir(exist_ok=True)
    icon.write_bytes(struct.pack('<HHH', 0, 1, len(sizes)) + b''.join(entries) + b''.join(images))
    return icon


def build(platform: str, package_dmg_flag: bool = False) -> None:
    generate_and_verify_notices()
    mac = platform.startswith('macos-')
    name = 'vcf-telegraf-helper' if mac else f'vcf-telegraf-helper-{platform}'
    options = ['--onedir', '--windowed', '--osx-bundle-identifier',
               'net.sentania.vcf-telegraf-helper'] if mac else ['--onefile']
    if mac:
        options += ['--icon', str(mac_icon())]
    elif platform == 'windows':
        options += ['--icon', str(windows_icon())]
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', *options, '--clean', '--noconfirm',
        '--name', name,
        '--copy-metadata', 'vcf-ops-telegraf-helper',
        '--collect-all', 'vcf_ops_telegraf_helper', '--collect-all', 'tzdata',
        '--collect-all', 'winrm', '--collect-all', 'truststore', '--collect-all', 'cryptography',
        '--hidden-import', 'PySide6.QtCore', '--hidden-import', 'PySide6.QtGui',
        '--hidden-import', 'PySide6.QtSvg', '--hidden-import', 'PySide6.QtWidgets', 'vcf_ops_telegraf_helper/cli/main.py',
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
        if package_dmg_flag:
            package_dmg(app, Path(f'dist/{name}.dmg'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('platform', choices=['linux', 'windows', 'macos-arm64', 'macos-x86_64'])
    parser.add_argument('--package-dmg', action='store_true',
                        help='Package the macOS .app bundle into a .dmg')
    args = parser.parse_args()
    build(args.platform, package_dmg_flag=args.package_dmg)
