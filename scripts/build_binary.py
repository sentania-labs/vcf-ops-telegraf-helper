"""Shared local, PR and release PyInstaller build definition."""
import argparse
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('platform', choices=['linux', 'windows', 'macos-arm64', 'macos-x86_64'])
args = parser.parse_args()
subprocess.run([
    sys.executable, '-m', 'PyInstaller', '--onefile', '--clean', '--noconfirm',
    '--name', f'vcf-telegraf-helper-{args.platform}',
    '--copy-metadata', 'vcf-ops-telegraf-helper',
    '--collect-all', 'vcf_ops_telegraf_helper', '--collect-all', 'tzdata',
    '--collect-all', 'winrm', '--collect-all', 'truststore',
    '--hidden-import', 'PySide6.QtCore', '--hidden-import', 'PySide6.QtGui',
    '--hidden-import', 'PySide6.QtWidgets', 'vcf_ops_telegraf_helper/cli/main.py',
], check=True)
