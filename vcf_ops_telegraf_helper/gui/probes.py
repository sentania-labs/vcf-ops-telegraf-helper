"""Endpoint inspection without reading or changing GUI widgets."""
from vcf_ops_telegraf_helper.models.endpoint import OSFamily
from vcf_ops_telegraf_helper.workflow.windows import detect_managed_installation, detect_windows_telegraf


def probe_endpoint(target, executor):
    if not executor.test_connection():
        raise RuntimeError('Connection failed: ' + str(getattr(executor, 'connection_error', None) or 'Unable to connect'))
    if target.os_family == OSFamily.WINDOWS:
        found = detect_windows_telegraf(executor)
        managed = detect_managed_installation(executor, found, read_config=False)
        if managed.present:
            managed = detect_managed_installation(executor, found, read_config=True)
        caption = executor.execute('(Get-CimInstance Win32_OperatingSystem).Caption', timeout=10)
        hostname = executor.execute('$env:COMPUTERNAME', timeout=10)
        result = dict(installed=found.installed, version=found.version or 'N/A', running=found.running,
                      config_dir=found.config_dir, binary=found.binary_path, service=found.service_name,
                      os=caption.stdout.strip().splitlines()[0] if caption.success and caption.stdout.strip() else 'Microsoft Windows',
                      arch='x86_64', managed=managed if managed.present else None)
    else:
        arch = executor.execute('uname -m', timeout=5)
        release = executor.execute('cat /etc/os-release', timeout=5)
        os_name = 'Unknown Linux'
        for line in release.stdout.splitlines() if release.success else []:
            if line.startswith('PRETTY_NAME='):
                os_name = line.split('=', 1)[1].strip('"\'')
                break
            if line.startswith('NAME='):
                os_name = line.split('=', 1)[1].strip('"\'')
        if release.success and release.stdout.strip() and '=' not in release.stdout:
            os_name = release.stdout.strip().splitlines()[0]
        binary = executor.execute('which telegraf', timeout=5)
        installed = binary.success and bool(binary.stdout.strip())
        version = executor.execute(f'{binary.stdout.strip()} version', timeout=5) if installed else None
        running = executor.execute('systemctl is-active telegraf', timeout=5)
        hostname = executor.execute('hostname -s', timeout=5)
        result = dict(installed=installed, version=version.stdout.strip() if version and version.success else 'N/A',
                      running=running.success and running.stdout.strip() == 'active', config_dir='/etc/telegraf/telegraf.d',
                      binary=None, service=None, os=os_name, arch=arch.stdout.strip() if arch.success else 'x86_64',
                      managed=None)
    names = hostname.stdout.strip().splitlines() if hostname.success else []
    result['hostname'] = names[-1].split('.')[0] if names else target.hostname
    return result
