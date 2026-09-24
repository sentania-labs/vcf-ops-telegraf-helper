"""Tests for local GUI server endpoints and template delivery."""

from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer
import requests

from vcf_ops_telegraf_helper.gui.server import HelperHTTPRequestHandler


def test_gui_server_routes():
    """Verify local GUI HTTP server serves HTML, CSS, and API endpoints."""
    # Spin up server on an ephemeral free port (port 0)
    server = ThreadingHTTPServer(("127.0.0.1", 0), HelperHTTPRequestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"

    try:
        # 1. Test index HTML delivery
        res_idx = requests.get(f"{base_url}/", timeout=5)
        assert res_idx.status_code == 200
        assert "text/html" in res_idx.headers["Content-Type"]
        assert "VCF Operations Open Telegraf Helper" in res_idx.text
        assert "lattice.css" in res_idx.text

        # 2. Test static CSS files
        res_css = requests.get(f"{base_url}/static/lattice.css", timeout=5)
        assert res_css.status_code == 200
        assert "text/css" in res_css.headers["Content-Type"]
        assert ".lat-btn" in res_css.text

        res_tokens = requests.get(f"{base_url}/static/tokens.css", timeout=5)
        assert res_tokens.status_code == 200
        assert "--accent:" in res_tokens.text

        # 3. Test API /api/vcf/validate (mock mode)
        res_vcf = requests.post(
            f"{base_url}/api/vcf/validate",
            json={"url": "https://vcf-ops.local", "mock": True},
            timeout=5,
        )
        assert res_vcf.status_code == 200
        vcf_data = res_vcf.json()
        assert vcf_data["valid"] is True

        # 4. Test API /api/endpoint/detect (mock mode)
        res_disc = requests.post(
            f"{base_url}/api/endpoint/detect",
            json={"hostname": "srv01.corp.local", "connection_method": "mock"},
            timeout=5,
        )
        assert res_disc.status_code == 200
        disc_data = res_disc.json()
        assert disc_data["success"] is True
        assert disc_data["discovery"]["telegraf_installed"] is True

        # 5. Test API /api/render
        res_render = requests.post(
            f"{base_url}/api/render",
            json={"collector": "10.0.0.1", "hostname": "srv01.corp.local", "cpu": True},
            timeout=5,
        )
        assert res_render.status_code == 200
        render_data = res_render.json()
        assert "[[inputs.cpu]]" in render_data["system_toml"]
        assert "[[outputs.http]]" in render_data["vcf_toml"]

        # 6. Test API /api/workflow/run (mock mode)
        res_run = requests.post(
            f"{base_url}/api/workflow/run",
            json={
                "vcf_url": "https://vcf-ops.local",
                "collector": "10.0.0.1",
                "hostname": "srv01.corp.local",
                "connection_method": "mock",
                "mock_vcf": True,
            },
            timeout=5,
        )
        assert res_run.status_code == 200
        run_data = res_run.json()
        assert run_data["success"] is True
        assert len(run_data["stages"]) == 8

    finally:
        server.shutdown()
        server.server_close()
