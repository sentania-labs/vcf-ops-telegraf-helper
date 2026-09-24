"""Local GUI server for VCF Operations Open Telegraf Helper.

Serves an ephemeral browser-based interface styled with sentania-labs/lattice.
Runs only while the administrator is interacting with the utility and exits on command.
"""

from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Any, Dict
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from vcf_ops_telegraf_helper.adapters.factory import get_adapter
from vcf_ops_telegraf_helper.adapters.mock import MockVCFOpsIntegration
from vcf_ops_telegraf_helper.executors.local import LocalExecutor
from vcf_ops_telegraf_helper.executors.mock import MockExecutor
from vcf_ops_telegraf_helper.executors.package import PackageExecutor
from vcf_ops_telegraf_helper.executors.ssh import SSHExecutor
from vcf_ops_telegraf_helper.models.endpoint import (
    ConnectionMethod,
    EndpointTarget,
    OSFamily,
)
from vcf_ops_telegraf_helper.models.monitoring import (
    CpuInputConfig,
    DiskInputConfig,
    MemInputConfig,
    MonitoringConfig,
    NetInputConfig,
    SwapInputConfig,
    SystemInputConfig,
)
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo, VCFEnvironment
from vcf_ops_telegraf_helper.models.workflow import DeploymentMode, WorkflowOptions
from vcf_ops_telegraf_helper.renderer.renderer import TelegrafRenderer
from vcf_ops_telegraf_helper.workflow.engine import ConfigureEndpointWorkflow


STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"


class HelperHTTPRequestHandler(BaseHTTPRequestHandler):
    """Handles static assets and API requests for the local helper GUI."""

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy standard HTTP access logs
        pass

    def _send_json(self, data: Dict[str, Any], status: int = 200) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self) -> Dict[str, Any]:
        content_len = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_len).decode("utf-8")
        return json.loads(raw) if raw else {}

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            index_file = TEMPLATES_DIR / "index.html"
            content = index_file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if path.startswith("/static/"):
            rel_name = path[len("/static/"):]
            static_file = STATIC_DIR / rel_name
            if static_file.exists() and static_file.is_file():
                content = static_file.read_bytes()
                mime = "text/css" if rel_name.endswith(".css") else "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

        self.send_error(404, "Not Found")

    def do_POST(self) -> None:
        path = self.path.split("?")[0]

        if path == "/api/vcf/validate":
            data = self._read_json()
            env = VCFEnvironment(
                name="gui",
                url=data.get("url", "https://vcf-ops.local"),
                username=data.get("username", "admin"),
                password=data.get("password") or None,
                collector=CollectorInfo(address=data.get("collector", "10.10.10.50")),
                verify_ssl=data.get("verify_ssl", False),
            )
            is_mock = data.get("mock", False)
            if is_mock:
                adapter = MockVCFOpsIntegration(env, connected=True, version="9.1.0 (simulated)")
            else:
                adapter = get_adapter(env)

            connected = adapter.validate_connection()
            ver = adapter.detect_version() if connected else "unknown"
            self._send_json({"valid": connected, "version": ver, "message": "OK" if connected else "Failed to connect"})
            return

        if path == "/api/endpoint/detect":
            data = self._read_json()
            conn_type = data.get("connection_method", "mock")
            hostname = data.get("hostname", "localhost")

            if conn_type == "mock":
                executor = MockExecutor(connected=True, telegraf_installed=True)
            elif conn_type == "local":
                executor = LocalExecutor()
            elif conn_type == "package":
                executor = PackageExecutor()
            else:
                executor = SSHExecutor(
                    hostname=hostname,
                    username=data.get("ssh_user", "root"),
                    password=data.get("ssh_pass") or None,
                    key_filename=data.get("ssh_key") or None,
                )

            # Test connection & detect
            if not executor.test_connection():
                self._send_json({"success": False, "message": "Cannot connect to endpoint"})
                return

            which_res = executor.execute("which telegraf")
            installed = which_res.success
            ver_res = executor.execute("telegraf version")
            version_str = ver_res.stdout.strip() if ver_res.success else None
            svc_res = executor.execute("systemctl is-active telegraf")
            svc_state = svc_res.stdout.strip() if svc_res.stdout else "unknown"

            disc = {
                "hostname": hostname,
                "os_name": "Linux",
                "os_version": "Ubuntu 24.04 LTS" if conn_type == "mock" else "Linux",
                "arch": "x86_64",
                "telegraf_installed": installed,
                "telegraf_version": version_str or "1.30.0",
                "service_state": svc_state,
            }
            self._send_json({"success": True, "discovery": disc})
            return

        if path == "/api/render":
            data = self._read_json()
            monitoring = MonitoringConfig(
                cpu=CpuInputConfig(enabled=data.get("cpu", True)),
                mem=MemInputConfig(enabled=data.get("mem", True)),
                disk=DiskInputConfig(enabled=data.get("disk", True)),
                net=NetInputConfig(enabled=data.get("net", True)),
                system=SystemInputConfig(enabled=data.get("system", True)),
                swap=SwapInputConfig(enabled=data.get("swap", True)),
            )
            system_toml = TelegrafRenderer.render_system_inputs(monitoring)
            vcf_toml = TelegrafRenderer.render_vcf_output(
                collector_address=data.get("collector", "10.10.10.50"),
                hostname=data.get("hostname", "target.local"),
                verify_ssl=data.get("verify_ssl", False),
            )
            self._send_json({"system_toml": system_toml, "vcf_toml": vcf_toml})
            return

        if path == "/api/workflow/run":
            data = self._read_json()
            env = VCFEnvironment(
                name="gui-run",
                url=data.get("vcf_url", "https://vcf-ops.local"),
                username=data.get("vcf_user", "admin"),
                password=data.get("vcf_pass") or None,
                collector=CollectorInfo(address=data.get("collector", "10.10.10.50")),
                verify_ssl=data.get("verify_ssl", False),
            )
            target = EndpointTarget(
                hostname=data.get("hostname", "localhost"),
                os_family=OSFamily.LINUX,
                connection_method=ConnectionMethod(data.get("connection_method", "mock")),
                username=data.get("ssh_user"),
                password=data.get("ssh_pass") or None,
                key_filename=data.get("ssh_key") or None,
            )
            monitoring = MonitoringConfig(
                cpu=CpuInputConfig(enabled=data.get("cpu", True)),
                mem=MemInputConfig(enabled=data.get("mem", True)),
                disk=DiskInputConfig(enabled=data.get("disk", True)),
                net=NetInputConfig(enabled=data.get("net", True)),
                system=SystemInputConfig(enabled=data.get("system", True)),
                swap=SwapInputConfig(enabled=data.get("swap", True)),
            )

            conn_method = target.connection_method
            if conn_method == ConnectionMethod.MOCK:
                executor = MockExecutor(connected=True, telegraf_installed=True)
            elif conn_method == ConnectionMethod.LOCAL:
                executor = LocalExecutor()
            elif conn_method == ConnectionMethod.PACKAGE:
                executor = PackageExecutor(output_dir="./vcf-gui-bundle")
            else:
                executor = SSHExecutor(
                    hostname=target.hostname,
                    username=target.username,
                    password=target.password,
                    key_filename=target.key_filename,
                )

            if data.get("mock_vcf", False):
                adapter = MockVCFOpsIntegration(env, connected=True)
            else:
                adapter = get_adapter(env)

            wf_options = WorkflowOptions(
                mode=DeploymentMode.PUSH if conn_method != ConnectionMethod.PACKAGE else DeploymentMode.SCRIPT,
                restart_service=True,
            )
            workflow = ConfigureEndpointWorkflow(
                environment=env,
                target=target,
                monitoring=monitoring,
                executor=executor,
                adapter=adapter,
                options=wf_options,
            )
            summary = workflow.run()
            self._send_json(summary.to_dict())
            return

        if path == "/api/shutdown":
            self._send_json({"status": "shutting_down"})
            server = self.server

            def _stop():
                server.shutdown()

            threading.Thread(target=_stop).start()
            return

        self.send_error(404, "Not Found")


def start_gui(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """Start local helper HTTP GUI and optionally open the user's web browser."""
    server = ThreadingHTTPServer((host, port), HelperHTTPRequestHandler)
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}"

    print(f"VCF Operations Open Telegraf Helper GUI started at: {url}")
    print("Press Ctrl+C or click 'Exit Helper' in the web interface to quit.")

    if open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down local helper GUI.")
    finally:
        server.server_close()
