import base64
import hashlib

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from vcf_ops_telegraf_helper.workflow.managed_encryption import decrypt_managed_config
from vcf_ops_telegraf_helper.workflow.windows import (
    MANAGED_ENCRYPTED_CONF, MANAGED_ENCRYPTED_PLUGINS, MANAGED_KEY,
    MANAGED_TELEGRAF_CONF, detect_managed_installation,
)
from test_managed_agent_discovery import _ManagedEndpoint
from vcf_ops_telegraf_helper.executors.base import CommandResult


def encrypted(data, key=b"synthetic key", pad=True):
    iv = bytes(range(16))
    padded = data + b" " * (16 - len(data) % 16) if pad else data
    cipher = Cipher(algorithms.AES(hashlib.sha256(key).digest()), modes.CBC(iv)).encryptor()
    return len(data).to_bytes(8, "little") + iv + cipher.update(padded) + cipher.finalize()


def test_decryption_truncates_padding_and_preserves_spaces():
    data = b"example configuration  "
    assert decrypt_managed_config(b"synthetic key", encrypted(data)) == data


def test_decryption_accepts_block_aligned_data_without_padding():
    data = b"0123456789abcdef" * 2
    assert decrypt_managed_config(b"synthetic key", encrypted(data, pad=False)) == data


def test_decryption_truncates_to_the_saved_size_whatever_the_padding_length():
    payload = encrypted(b"example" + b" " * 40)
    assert decrypt_managed_config(b"synthetic key", (7).to_bytes(8, "little") + payload[8:]) == b"example"


def test_decryption_rejects_wrong_key_and_bad_lengths():
    payload = encrypted(b"example configuration for a wrong key")
    with pytest.raises(ValueError, match="decryption failed"):
        decrypt_managed_config(b"wrong key", payload)
    with pytest.raises(ValueError, match="truncated"):
        decrypt_managed_config(b"synthetic key", payload[:-1])
    with pytest.raises(ValueError, match="size"):
        decrypt_managed_config(b"synthetic key", (100).to_bytes(8, "little") + payload[8:])


class EncryptedEndpoint(_ManagedEndpoint):
    def execute(self, command, timeout=30):
        if "ReadAllBytes" in command:
            path = command.split("'")[1]
            return CommandResult(exit_code=0, stdout=base64.b64encode(self.files[path]).decode(), command=command)
        return super().execute(command, timeout)


def test_encrypted_config_and_optional_plugins_are_read(caplog):
    endpoint = EncryptedEndpoint()
    del endpoint.files[MANAGED_TELEGRAF_CONF]
    endpoint.files.update({MANAGED_KEY: b"synthetic key", MANAGED_ENCRYPTED_CONF: encrypted(b"[agent]\n"),
                           MANAGED_ENCRYPTED_PLUGINS: encrypted(b'{"plugins": []}')})
    found = detect_managed_installation(endpoint)
    assert found.telegraf_conf == "[agent]\n"
    assert found.plugins_config == '{"plugins": []}'
    assert not found.read_errors
    assert "encrypted file" in caplog.text


def test_missing_config_and_key_are_reported():
    endpoint = EncryptedEndpoint()
    del endpoint.files[MANAGED_TELEGRAF_CONF]
    found = detect_managed_installation(endpoint)
    assert "configuration is missing" in found.read_errors[0]
    endpoint.files[MANAGED_ENCRYPTED_CONF] = encrypted(b"example")
    found = detect_managed_installation(endpoint)
    assert "key.pem is missing" in found.read_errors[0]


def test_takeover_backs_up_decrypted_files(tmp_path):
    from pathlib import Path
    from test_takeover import ManagedWindowsEndpoint, _workflow, FIXTURE

    class Endpoint(ManagedWindowsEndpoint):
        def execute(self, command, timeout=30):
            if "ReadAllBytes" in command:
                path = command.split("'")[1]
                return CommandResult(exit_code=0, stdout=base64.b64encode(self.files[path]).decode(), command=command)
            return super().execute(command, timeout)

    endpoint = Endpoint()
    del endpoint.files[MANAGED_TELEGRAF_CONF]
    endpoint.files.update({MANAGED_KEY: b"synthetic key", MANAGED_ENCRYPTED_CONF: encrypted(FIXTURE.read_bytes()),
                           MANAGED_ENCRYPTED_PLUGINS: encrypted(b'{"plugins": []}')})
    workflow = _workflow(tmp_path, endpoint=endpoint)
    summary = workflow.run()
    assert summary.success, [(stage.stage, stage.message) for stage in summary.stages]
    backup = Path(workflow.record.backup_dir)
    assert (backup / "telegraf.conf").read_text() == FIXTURE.read_text()
    assert (backup / "plugins" / "telegraf").read_text() == '{"plugins": []}'
