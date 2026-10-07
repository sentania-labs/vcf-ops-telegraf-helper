"""Exercise real SSH authentication, sudo reads and apt purge in an isolated container."""
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import uuid

from vcf_ops_telegraf_helper.executors.ssh import SSHExecutor
from vcf_ops_telegraf_helper.models.endpoint import EndpointTarget
from vcf_ops_telegraf_helper.models.workflow import StageStatus
from vcf_ops_telegraf_helper.workflow.uninstall import UninstallEndpointWorkflow

name = 'telegraf-regression-' + uuid.uuid4().hex[:12]
password = secrets.token_urlsafe(24)
container = None
try:
    with tempfile.TemporaryDirectory(prefix=name) as directory:
        Path(directory, 'Dockerfile').write_text('''FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server sudo && rm -rf /var/lib/apt/lists/*
RUN useradd -m helpertest && echo 'helpertest ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/helpertest && mkdir /run/sshd
RUN mkdir -p /tmp/pkg/DEBIAN /tmp/pkg/usr/bin && printf 'Package: telegraf\\nVersion: 1.0\\nArchitecture: all\\nMaintainer: Test <test@example.invalid>\\nDescription: Synthetic purge regression fixture\\n' > /tmp/pkg/DEBIAN/control && printf '#!/bin/sh\\nexit 0\\n' > /tmp/pkg/usr/bin/telegraf && chmod +x /tmp/pkg/usr/bin/telegraf && dpkg-deb --build /tmp/pkg /tmp/telegraf.deb && dpkg -i /tmp/telegraf.deb
RUN groupadd telegraf && useradd -g telegraf telegraf && mkdir -p /etc/telegraf/telegraf.d && echo protected-test-key > /etc/telegraf/telegraf.d/key.pem && chown root:telegraf /etc/telegraf/telegraf.d/key.pem && chmod 640 /etc/telegraf/telegraf.d/key.pem
# Container has no systemd; only its service-manager call is stubbed.
RUN printf '#!/bin/sh\\nexit 0\\n' > /usr/local/bin/systemctl && chmod +x /usr/local/bin/systemctl
CMD ["/usr/sbin/sshd", "-D", "-e"]
''')
        subprocess.run(['docker', 'build', '-q', '-t', name, directory], check=True)
        container = subprocess.check_output(['docker', 'run', '-d', '--rm', '-p', '127.0.0.1::22', name], text=True).strip()
        subprocess.run(['docker', 'exec', '-i', container, 'chpasswd'], input=f'helpertest:{password}\n', text=True, check=True)
        ports = json.loads(subprocess.check_output(['docker', 'inspect', '--format', '{{json .NetworkSettings.Ports}}', container], text=True))
        port = int(ports['22/tcp'][0]['HostPort'])
        executor = SSHExecutor('127.0.0.1', port=port, username='helpertest', password=password)
        assert executor.test_connection(), executor.connection_error
        assert executor.download('/etc/telegraf/telegraf.d/key.pem').strip() == 'protected-test-key'
        wrong = SSHExecutor('127.0.0.1', port=port, username='helpertest', password='incorrect-test-password')
        assert not wrong.test_connection()
        workflow = UninstallEndpointWorkflow(EndpointTarget(hostname='127.0.0.1', username='helpertest'), executor)
        result = workflow.remove_package()
        assert result.status == StageStatus.PASS, result.message
        assert executor.execute(workflow._package_check()).stdout.strip() == 'ABSENT'
        assert not executor.execute('id telegraf').success
        assert workflow.remove_package().status == StageStatus.PASS, "Repeat purge must also handle an absent package"
        executor.close()
        wrong.close()
        print('PASS: real SSH password auth, protected sudo read, apt purge and account removal')
finally:
    if container:
        subprocess.run(['docker', 'stop', container], check=False, stdout=subprocess.DEVNULL)
    subprocess.run(['docker', 'image', 'rm', name], check=False, stdout=subprocess.DEVNULL)
