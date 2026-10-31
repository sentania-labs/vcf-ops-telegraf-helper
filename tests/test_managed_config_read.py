"""Managed configuration capture with optional fragment directories (issue #88)."""

import subprocess
from dataclasses import replace

import pytest

from vcf_ops_telegraf_helper.executors.base import CommandResult
from vcf_ops_telegraf_helper.workflow.managed import (
    LINUX_PATHS, WINDOWS_PATHS, ManagedInstallation, read_managed_config,
)


PROGRESS = ('#< CLIXML\n<Objs xmlns="http://schemas.microsoft.com/powershell/2004/04">'
            '<Obj S="progress"><MS><S N="Activity">Preparing modules for first use.</S>'
            '</MS></Obj>{}</Objs>')


class CaptureExecutor:
    def __init__(self, paths, *, directory_exists=True, failure=None):
        self.paths = paths
        self.directory_exists = directory_exists
        self.failure = failure

    def file_exists(self, path):
        return path == self.paths.conf

    def download(self, path):
        assert path == self.paths.conf
        return "[agent]\n"

    def execute(self, command, timeout=10):
        if self.paths.is_windows:
            assert "Get-ChildItem" in command
            if self.failure is not None:
                return self.failure
            # Emulate pywinrm: an unguarded missing-path listing exits 1.
            guarded = f"if (Test-Path '{self.paths.fragments}')" in command
            if not self.directory_exists and not guarded:
                return CommandResult(exit_code=1, stderr=PROGRESS.format(""))
            return CommandResult(exit_code=0)
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
        return CommandResult(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)


@pytest.mark.parametrize("directory_exists", [False, True], ids=["missing", "empty"])
def test_windows_optional_fragment_directory(directory_exists):
    found = ManagedInstallation(WINDOWS_PATHS)
    read_managed_config(CaptureExecutor(found.paths, directory_exists=directory_exists), found)
    assert found.telegraf_conf == "[agent]\n"
    assert found.telegraf_d == {}
    assert found.read_errors == []


@pytest.mark.parametrize(("stderr", "reason"), [
    (PROGRESS.format('<S S="Error">Access denied._x000D__x000A_Contact admin.</S>'),
     "Access denied.\r\nContact admin."),
    (PROGRESS.format(""), "command failed (exit 1)"),
    ('#< CLIXML\n<Objs broken', "command failed (exit 1)"),
    ("", "command failed (exit 1)"),
    ("Permission denied", "Permission denied"),
])
def test_listing_failure_has_readable_reason(stderr, reason):
    found = ManagedInstallation(WINDOWS_PATHS)
    failure = CommandResult(exit_code=1, stderr=stderr)
    read_managed_config(CaptureExecutor(found.paths, failure=failure), found)
    assert found.read_errors == [f"Could not list {found.paths.fragments}: {reason}"]


def test_encrypted_config_read_failure_decodes_clixml():
    class EncryptedExecutor(CaptureExecutor):
        def file_exists(self, path):
            return path in (self.paths.encrypted_conf, self.paths.key)

        def execute(self, command, timeout=10):
            if "ReadAllBytes" in command:
                return CommandResult(exit_code=1, stderr=PROGRESS.format(
                    '<S S="Error">Key read denied._x000D__x000A_</S>'))
            return super().execute(command, timeout)

    found = ManagedInstallation(WINDOWS_PATHS)
    read_managed_config(EncryptedExecutor(found.paths), found)
    assert len(found.read_errors) == 1
    assert "Key read denied." in found.read_errors[0]
    assert "CLIXML" not in found.read_errors[0]
    assert "<Objs" not in found.read_errors[0]


def test_linux_missing_fragment_directory(tmp_path):
    paths = replace(LINUX_PATHS, fragments=str(tmp_path / "missing" / "telegraf.d"))
    found = ManagedInstallation(paths)
    read_managed_config(CaptureExecutor(paths), found)
    assert found.telegraf_conf == "[agent]\n"
    assert found.telegraf_d == {}
    assert found.read_errors == []
