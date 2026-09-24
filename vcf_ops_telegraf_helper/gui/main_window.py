"""Lattice-styled native PySide6 MainWindow for VCF Operations Open Telegraf Helper.

Provides the complete 5-step guided onboarding workflow:
1. VCF Operations environment configuration and validation
2. Endpoint target connection and discovery
3. Monitoring inputs and deployment mode selection
4. Configuration review and TOML preview
5. Live workflow execution, progress streaming, and honest verification
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from vcf_ops_telegraf_helper.adapters.factory import get_adapter
from vcf_ops_telegraf_helper.adapters.mock import MockVCFOpsIntegration
from vcf_ops_telegraf_helper.executors.local import LocalExecutor
from vcf_ops_telegraf_helper.executors.mock import MockExecutor
from vcf_ops_telegraf_helper.executors.package import PackageExecutor
from vcf_ops_telegraf_helper.executors.ssh import SSHExecutor
from vcf_ops_telegraf_helper.gui.theme import build_stylesheet
from vcf_ops_telegraf_helper.models.endpoint import ConnectionMethod, EndpointTarget
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
from vcf_ops_telegraf_helper.models.workflow import (
    DeploymentMode,
    RunSummary,
    StageResult,
    WorkflowOptions,
    WorkflowStage,
)
from vcf_ops_telegraf_helper.renderer.renderer import TelegrafRenderer
from vcf_ops_telegraf_helper.storage.state import StateStore
from vcf_ops_telegraf_helper.workflow.engine import ConfigureEndpointWorkflow


class WorkflowWorker(QObject):
    """Background worker executing the ConfigureEndpointWorkflow to keep Qt event loop responsive."""

    stage_updated = Signal(object)  # StageResult
    finished = Signal(object)  # RunSummary
    failed = Signal(str)

    def __init__(self, workflow: ConfigureEndpointWorkflow) -> None:
        super().__init__()
        self.workflow = workflow

    def run(self) -> None:
        try:
            summary = self.workflow.run(progress_callback=self._on_stage)
            self.finished.emit(summary)
        except Exception as exc:
            self.failed.emit(str(exc))

    def _on_stage(self, stage: WorkflowStage, result: StageResult) -> None:
        self.stage_updated.emit(result)


class MainWindow(QMainWindow):
    """Main window for the native Lattice-styled administrator helper."""

    def __init__(self, state_store: Optional[StateStore] = None) -> None:
        super().__init__()
        self.state_store = state_store or StateStore()
        self.current_theme = "dark"
        self.last_summary: Optional[RunSummary] = None
        self.worker_thread: Optional[QThread] = None

        self.setWindowTitle("VCF Operations Open Telegraf Helper")
        self.resize(1020, 720)
        self.setMinimumSize(880, 600)

        self._init_ui()
        self._apply_theme()
        self._load_saved_state()

    def _apply_theme(self) -> None:
        self.setStyleSheet(build_stylesheet(self.current_theme))
        if hasattr(self, "theme_btn"):
            self.theme_btn.setText("Theme: Light" if self.current_theme == "dark" else "Theme: Dark")

    def _toggle_theme(self) -> None:
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self._apply_theme()

    def _init_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header bar (Lattice chrome)
        header = QFrame()
        header.setProperty("class", "lattice-header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 12, 18, 12)

        title_label = QLabel("VCF Operations Open Telegraf Helper")
        title_label.setProperty("class", "lattice-title")
        subtitle_label = QLabel("v0.1.0  |  Broadcom Supported Workflow  |  Local Utility")
        subtitle_label.setProperty("class", "lattice-caption")

        header_title_col = QVBoxLayout()
        header_title_col.setSpacing(2)
        header_title_col.addWidget(title_label)
        header_title_col.addWidget(subtitle_label)

        header_layout.addLayout(header_title_col)
        header_layout.addStretch()

        self.theme_btn = QPushButton("Theme: Light")
        self.theme_btn.clicked.connect(self._toggle_theme)
        header_layout.addWidget(self.theme_btn)

        main_layout.addWidget(header)

        # Content area: left sidebar navigation, right step stack
        body_layout = QHBoxLayout()
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        # Sidebar navigation
        sidebar = QFrame()
        sidebar.setFixedWidth(220)
        sidebar.setProperty("class", "lattice-sidebar")
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(12, 16, 12, 16)
        side_layout.setSpacing(8)

        nav_label = QLabel("WORKFLOW STEPS")
        nav_label.setProperty("class", "lattice-section-label")
        side_layout.addWidget(nav_label)

        self.step_list = QListWidget()
        self.step_list.setProperty("class", "step-list")
        steps = [
            "1. VCF Operations",
            "2. Endpoint Target",
            "3. Monitoring Inputs",
            "4. Review & Preview",
            "5. Execute & Verify",
        ]
        for s in steps:
            item = QListWidgetItem(s)
            self.step_list.addItem(item)
        self.step_list.setCurrentRow(0)
        self.step_list.currentRowChanged.connect(self._on_step_changed)
        side_layout.addWidget(self.step_list)

        side_layout.addStretch()

        status_box = QFrame()
        status_box.setProperty("class", "lattice-card")
        sb_layout = QVBoxLayout(status_box)
        sb_layout.setContentsMargins(8, 8, 8, 8)
        sb_label = QLabel("SYSTEM CONTEXT")
        sb_label.setProperty("class", "lattice-section-label")
        sb_layout.addWidget(sb_label)
        self.sb_text = QLabel("Mode: Local Push\nVCF: 9.1 Compatibility\nState: Local JSON")
        self.sb_text.setProperty("class", "lattice-caption")
        sb_layout.addWidget(self.sb_text)
        side_layout.addWidget(status_box)

        body_layout.addWidget(sidebar)

        # Right Stacked Pages
        self.page_stack = QStackedWidget()
        self.page_stack.addWidget(self._build_step1_page())
        self.page_stack.addWidget(self._build_step2_page())
        self.page_stack.addWidget(self._build_step3_page())
        self.page_stack.addWidget(self._build_step4_page())
        self.page_stack.addWidget(self._build_step5_page())

        body_layout.addWidget(self.page_stack, 1)
        main_layout.addLayout(body_layout, 1)

    def _on_step_changed(self, row: int) -> None:
        if row == 3:  # Review & Preview
            self._update_preview()
        self.page_stack.setCurrentIndex(row)

    # --------------------------------------------------------------------------
    # Step 1: VCF Operations
    # --------------------------------------------------------------------------
    def _build_step1_page(self) -> QWidget:
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        card = QFrame()
        card.setProperty("class", "lattice-card")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(12)

        lbl = QLabel("STEP 1: VCF OPERATIONS ENVIRONMENT")
        lbl.setProperty("class", "lattice-section-label")
        c_layout.addWidget(lbl)

        desc = QLabel(
            "Configure the target VCF Operations 9.1 environment and Cloud Proxy metrics collector. "
            "Telemetry is routed to Cloud Proxy on port 443 via Broadcom Wavefront format."
        )
        desc.setProperty("class", "lattice-muted")
        desc.setWordWrap(True)
        c_layout.addWidget(desc)

        grid = QGridLayout()
        grid.setSpacing(10)

        grid.addWidget(QLabel("VCF Operations URL:"), 0, 0)
        self.vcf_url_input = QLineEdit("https://vcf-ops.local")
        grid.addWidget(self.vcf_url_input, 0, 1)

        grid.addWidget(QLabel("Cloud Proxy Collector IP/FQDN:"), 1, 0)
        self.vcf_collector_input = QLineEdit("10.10.10.50")
        grid.addWidget(self.vcf_collector_input, 1, 1)

        grid.addWidget(QLabel("Authentication Token:"), 2, 0)
        self.vcf_token_input = QLineEdit()
        self.vcf_token_input.setPlaceholderText("Paste token or enter username/password below")
        self.vcf_token_input.setEchoMode(QLineEdit.Password)
        grid.addWidget(self.vcf_token_input, 2, 1)

        grid.addWidget(QLabel("Username (Optional):"), 3, 0)
        self.vcf_user_input = QLineEdit()
        grid.addWidget(self.vcf_user_input, 3, 1)

        grid.addWidget(QLabel("Password (Optional):"), 4, 0)
        self.vcf_pass_input = QLineEdit()
        self.vcf_pass_input.setEchoMode(QLineEdit.Password)
        grid.addWidget(self.vcf_pass_input, 4, 1)

        c_layout.addLayout(grid)

        self.vcf_ssl_check = QCheckBox("Verify TLS certificates (disable for self-signed lab certs)")
        c_layout.addWidget(self.vcf_ssl_check)

        self.mock_vcf_check = QCheckBox("Simulate VCF Operations (Offline test adapter)")
        c_layout.addWidget(self.mock_vcf_check)

        btn_row = QHBoxLayout()
        self.test_vcf_btn = QPushButton("Validate VCF Connection")
        self.test_vcf_btn.clicked.connect(self._test_vcf_connection)
        btn_row.addWidget(self.test_vcf_btn)

        self.vcf_status_label = QLabel("Status: Not checked")
        self.vcf_status_label.setProperty("class", "lattice-caption")
        btn_row.addWidget(self.vcf_status_label)
        btn_row.addStretch()

        next_btn = QPushButton("Next: Endpoint Target ->")
        next_btn.setProperty("class", "primary")
        next_btn.clicked.connect(lambda: self.step_list.setCurrentRow(1))
        btn_row.addWidget(next_btn)

        c_layout.addLayout(btn_row)
        layout.addWidget(card)
        layout.addStretch()

        scroll.setWidget(content)
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(scroll)
        return page

    def _test_vcf_connection(self) -> None:
        self.vcf_status_label.setText("Testing connection...")
        try:
            env = self._get_vcf_env()
            if self.mock_vcf_check.isChecked():
                adapter = MockVCFOpsIntegration(env=env)
            else:
                adapter = get_adapter(env)
            valid = adapter.validate_connection()
            if valid:
                self.vcf_status_label.setText("Status: PASS (Connected)")
                self.vcf_status_label.setStyleSheet("color: #199e70; font-weight: 600;")
                self.state_store.save_environment(env)
            else:
                self.vcf_status_label.setText("Status: FAIL (Connection refused or unreachable)")
                self.vcf_status_label.setStyleSheet("color: #d95926; font-weight: 600;")
        except Exception as exc:
            self.vcf_status_label.setText(f"Error: {exc}")
            self.vcf_status_label.setStyleSheet("color: #d95926; font-weight: 600;")

    # --------------------------------------------------------------------------
    # Step 2: Endpoint Target
    # --------------------------------------------------------------------------
    def _build_step2_page(self) -> QWidget:
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        card = QFrame()
        card.setProperty("class", "lattice-card")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(12)

        lbl = QLabel("STEP 2: ENDPOINT TARGET & DETECTION")
        lbl.setProperty("class", "lattice-section-label")
        c_layout.addWidget(lbl)

        desc = QLabel(
            "Define the host where open-source Telegraf is or will be running. "
            "The helper will detect OS, architecture, existing Telegraf version, and service state."
        )
        desc.setProperty("class", "lattice-muted")
        desc.setWordWrap(True)
        c_layout.addWidget(desc)

        grid = QGridLayout()
        grid.setSpacing(10)

        grid.addWidget(QLabel("Hostname or IP Address:"), 0, 0)
        self.ep_host_input = QLineEdit("10.10.10.101")
        grid.addWidget(self.ep_host_input, 0, 1)

        grid.addWidget(QLabel("Connection Method:"), 1, 0)
        self.ep_method_combo = QComboBox()
        self.ep_method_combo.addItems(["SSH (Linux Remote)", "Mock (Simulated)", "Local Subprocess", "Package Script Bundle"])
        grid.addWidget(self.ep_method_combo, 1, 1)

        grid.addWidget(QLabel("SSH Username:"), 2, 0)
        self.ep_user_input = QLineEdit("root")
        grid.addWidget(self.ep_user_input, 2, 1)

        grid.addWidget(QLabel("SSH Password:"), 3, 0)
        self.ep_pass_input = QLineEdit()
        self.ep_pass_input.setEchoMode(QLineEdit.Password)
        grid.addWidget(self.ep_pass_input, 3, 1)

        grid.addWidget(QLabel("SSH Key Path:"), 4, 0)
        self.ep_key_input = QLineEdit("~/.ssh/id_rsa")
        grid.addWidget(self.ep_key_input, 4, 1)

        c_layout.addLayout(grid)

        det_row = QHBoxLayout()
        self.detect_ep_btn = QPushButton("Detect Endpoint")
        self.detect_ep_btn.clicked.connect(self._detect_endpoint)
        det_row.addWidget(self.detect_ep_btn)

        self.ep_status_label = QLabel("Not detected yet")
        self.ep_status_label.setProperty("class", "lattice-caption")
        det_row.addWidget(self.ep_status_label)
        det_row.addStretch()
        c_layout.addLayout(det_row)

        self.ep_details_box = QPlainTextEdit()
        self.ep_details_box.setProperty("class", "code-block")
        self.ep_details_box.setReadOnly(True)
        self.ep_details_box.setMaximumHeight(100)
        self.ep_details_box.setPlainText("Endpoint details will appear here after detection.")
        c_layout.addWidget(self.ep_details_box)

        btn_row = QHBoxLayout()
        back_btn = QPushButton("<- Back: VCF Ops")
        back_btn.clicked.connect(lambda: self.step_list.setCurrentRow(0))
        btn_row.addWidget(back_btn)
        btn_row.addStretch()

        next_btn = QPushButton("Next: Monitoring Inputs ->")
        next_btn.setProperty("class", "primary")
        next_btn.clicked.connect(lambda: self.step_list.setCurrentRow(2))
        btn_row.addWidget(next_btn)
        c_layout.addLayout(btn_row)

        layout.addWidget(card)
        layout.addStretch()

        scroll.setWidget(content)
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(scroll)
        return page

    def _detect_endpoint(self) -> None:
        self.ep_status_label.setText("Detecting...")
        try:
            target = self._get_endpoint_target()
            executor = self._create_executor(target)
            connected = executor.test_connection()
            if not connected:
                self.ep_status_label.setText("Connection failed: unable to connect")
                self.ep_status_label.setStyleSheet("color: #d95926;")
                return

            arch_res = executor.execute("uname -m", timeout=5)
            arch = arch_res.stdout.strip() if arch_res.success else "x86_64"

            os_rel = executor.execute("cat /etc/os-release", timeout=5)
            os_version = "Unknown Linux"
            if os_rel.success:
                for line in os_rel.stdout.splitlines():
                    if line.startswith("PRETTY_NAME="):
                        os_version = line.split("=", 1)[1].strip('"')
                        break

            which_res = executor.execute("which telegraf", timeout=5)
            installed = which_res.success
            telegraf_bin = which_res.stdout.strip() if installed else "/usr/bin/telegraf"

            version_str = "N/A"
            if installed:
                ver_res = executor.execute(f"{telegraf_bin} version", timeout=5)
                if ver_res.success:
                    version_str = ver_res.stdout.strip()

            svc_res = executor.execute("systemctl is-active telegraf", timeout=5)
            running = svc_res.success and svc_res.stdout.strip() == "active"

            self.ep_status_label.setText("Connected & Discovered")
            self.ep_status_label.setStyleSheet("color: #199e70; font-weight: 600;")

            details = [
                f"OS: {os_version}",
                f"Architecture: {arch}",
                f"Telegraf Installed: {'YES' if installed else 'NO'}",
                f"Telegraf Version: {version_str}",
                f"Service Running: {'YES' if running else 'NO'}",
                "Config Directory: /etc/telegraf/telegraf.d",
            ]
            self.ep_details_box.setPlainText("\n".join(details))
            self.state_store.record_endpoint(target.hostname)
        except Exception as exc:
            self.ep_status_label.setText(f"Detection error: {exc}")
            self.ep_status_label.setStyleSheet("color: #d95926;")

    # --------------------------------------------------------------------------
    # Step 3: Monitoring Inputs
    # --------------------------------------------------------------------------
    def _build_step3_page(self) -> QWidget:
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        card = QFrame()
        card.setProperty("class", "lattice-card")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(14)

        lbl = QLabel("STEP 3: MONITORING INPUTS & DEPLOYMENT MODE")
        lbl.setProperty("class", "lattice-section-label")
        c_layout.addWidget(lbl)

        desc = QLabel(
            "Select standard system telemetry inputs for VCF Operations. "
            "All generated configuration conforms to Broadcom's documented schema and defaults."
        )
        desc.setProperty("class", "lattice-muted")
        desc.setWordWrap(True)
        c_layout.addWidget(desc)

        sec_in = QLabel("METRIC INPUTS")
        sec_in.setProperty("class", "lattice-section-label")
        c_layout.addWidget(sec_in)

        self.cpu_check = QCheckBox("CPU Metrics (inputs.cpu: percpu, totalcpu, collect_cpu_time, report_active)")
        self.cpu_check.setChecked(True)
        c_layout.addWidget(self.cpu_check)

        self.mem_check = QCheckBox("Memory Metrics (inputs.mem: system memory usage and percentages)")
        self.mem_check.setChecked(True)
        c_layout.addWidget(self.mem_check)

        self.disk_check = QCheckBox("Disk Usage (inputs.disk: mount points, excluding pseudo/virtual fs)")
        self.disk_check.setChecked(True)
        c_layout.addWidget(self.disk_check)

        self.net_check = QCheckBox("Network Interface Metrics (inputs.net: bandwidth and error counts)")
        self.net_check.setChecked(True)
        c_layout.addWidget(self.net_check)

        self.sys_check = QCheckBox("System Load & Uptime (inputs.system: 1m/5m/15m load averages)")
        self.sys_check.setChecked(True)
        c_layout.addWidget(self.sys_check)

        self.swap_check = QCheckBox("Swap Usage (inputs.swap: swap in/out and space utilization)")
        self.swap_check.setChecked(True)
        c_layout.addWidget(self.swap_check)

        sec_mode = QLabel("DEPLOYMENT MODE")
        sec_mode.setProperty("class", "lattice-section-label")
        c_layout.addWidget(sec_mode)

        self.mode_group = QButtonGroup(self)
        self.mode_push = QRadioButton("Direct Push: Helper automatically applies config and restarts Telegraf")
        self.mode_push.setChecked(True)
        self.mode_script = QRadioButton("Generate Bundle: Creates auditable script package (apply.sh + configs)")
        self.mode_conf = QRadioButton("Configuration Only: Preview and render files without target changes")

        self.mode_group.addButton(self.mode_push, 0)
        self.mode_group.addButton(self.mode_script, 1)
        self.mode_group.addButton(self.mode_conf, 2)

        c_layout.addWidget(self.mode_push)
        c_layout.addWidget(self.mode_script)
        c_layout.addWidget(self.mode_conf)

        btn_row = QHBoxLayout()
        back_btn = QPushButton("<- Back: Endpoint")
        back_btn.clicked.connect(lambda: self.step_list.setCurrentRow(1))
        btn_row.addWidget(back_btn)
        btn_row.addStretch()

        next_btn = QPushButton("Next: Review & Preview ->")
        next_btn.setProperty("class", "primary")
        next_btn.clicked.connect(lambda: self.step_list.setCurrentRow(3))
        btn_row.addWidget(next_btn)
        c_layout.addLayout(btn_row)

        layout.addWidget(card)
        layout.addStretch()

        scroll.setWidget(content)
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(scroll)
        return page

    # --------------------------------------------------------------------------
    # Step 4: Review & Preview
    # --------------------------------------------------------------------------
    def _build_step4_page(self) -> QWidget:
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        card = QFrame()
        card.setProperty("class", "lattice-card")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(12)

        lbl = QLabel("STEP 4: CONFIGURATION REVIEW & PREVIEW")
        lbl.setProperty("class", "lattice-section-label")
        c_layout.addWidget(lbl)

        desc = QLabel(
            "Review generated Telegraf TOML fragments and planned actions before any execution. "
            "No endpoints are contacted during preview."
        )
        desc.setProperty("class", "lattice-muted")
        desc.setWordWrap(True)
        c_layout.addWidget(desc)

        self.review_summary_box = QPlainTextEdit()
        self.review_summary_box.setProperty("class", "code-block")
        self.review_summary_box.setReadOnly(True)
        self.review_summary_box.setMaximumHeight(80)
        c_layout.addWidget(self.review_summary_box)

        lbl_sys = QLabel("GENERATED SYSTEM INPUTS (vcf-helper-system.conf):")
        lbl_sys.setProperty("class", "lattice-caption")
        c_layout.addWidget(lbl_sys)

        self.preview_system_box = QPlainTextEdit()
        self.preview_system_box.setProperty("class", "code-block")
        self.preview_system_box.setReadOnly(True)
        self.preview_system_box.setMinimumHeight(160)
        c_layout.addWidget(self.preview_system_box)

        lbl_vcf = QLabel("GENERATED CLOUD PROXY OUTPUT (cloudproxy-http.conf):")
        lbl_vcf.setProperty("class", "lattice-caption")
        c_layout.addWidget(lbl_vcf)

        self.preview_output_box = QPlainTextEdit()
        self.preview_output_box.setProperty("class", "code-block")
        self.preview_output_box.setReadOnly(True)
        self.preview_output_box.setMinimumHeight(160)
        c_layout.addWidget(self.preview_output_box)

        btn_row = QHBoxLayout()
        back_btn = QPushButton("<- Back: Inputs")
        back_btn.clicked.connect(lambda: self.step_list.setCurrentRow(2))
        btn_row.addWidget(back_btn)

        refresh_btn = QPushButton("Refresh Preview")
        refresh_btn.clicked.connect(self._update_preview)
        btn_row.addWidget(refresh_btn)
        btn_row.addStretch()

        next_btn = QPushButton("Proceed to Execution ->")
        next_btn.setProperty("class", "primary")
        next_btn.clicked.connect(lambda: self.step_list.setCurrentRow(4))
        btn_row.addWidget(next_btn)
        c_layout.addLayout(btn_row)

        layout.addWidget(card)
        layout.addStretch()

        scroll.setWidget(content)
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(scroll)
        return page

    def _update_preview(self) -> None:
        target = self._get_endpoint_target()
        env = self._get_vcf_env()
        mon = self._get_monitoring_config()
        mode = self._get_deployment_mode()

        renderer = TelegrafRenderer()
        sys_toml = renderer.render_system_inputs(mon)
        out_toml = renderer.render_vcf_output(
            collector_address=env.collector.address,
            hostname=target.hostname,
            verify_ssl=env.verify_ssl,
        )

        summary_lines = [
            f"Target: {target.hostname} ({target.connection_method.value})",
            f"VCF Collector: {env.collector.address} (SSL Verify: {env.verify_ssl})",
            f"Deployment Mode: {mode.value}",
            "Managed Fragments: /etc/telegraf/telegraf.d/vcf-helper-system.conf, /etc/telegraf/telegraf.d/cloudproxy-http.conf",
        ]
        self.review_summary_box.setPlainText("\n".join(summary_lines))
        self.preview_system_box.setPlainText(sys_toml)
        self.preview_output_box.setPlainText(out_toml)

    # --------------------------------------------------------------------------
    # Step 5: Execution & Honest Verification
    # --------------------------------------------------------------------------
    def _build_step5_page(self) -> QWidget:
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        card = QFrame()
        card.setProperty("class", "lattice-card")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(12)

        lbl = QLabel("STEP 5: EXECUTION & VERIFICATION")
        lbl.setProperty("class", "lattice-section-label")
        c_layout.addWidget(lbl)

        desc = QLabel(
            "Execute the guided 8-stage onboarding workflow. "
            "Stage results and operational verifications are reported honestly without masking failure domains."
        )
        desc.setProperty("class", "lattice-muted")
        desc.setWordWrap(True)
        c_layout.addWidget(desc)

        action_row = QHBoxLayout()
        self.dry_run_check = QCheckBox("Dry-run only (Simulate without target modifications)")
        action_row.addWidget(self.dry_run_check)

        self.execute_btn = QPushButton("Execute Guided Workflow")
        self.execute_btn.setProperty("class", "primary")
        self.execute_btn.clicked.connect(self._run_workflow)
        action_row.addWidget(self.execute_btn)
        action_row.addStretch()

        self.export_md_btn = QPushButton("Export Markdown")
        self.export_md_btn.setEnabled(False)
        self.export_md_btn.clicked.connect(self._export_markdown)
        action_row.addWidget(self.export_md_btn)

        self.export_json_btn = QPushButton("Export JSON")
        self.export_json_btn.setEnabled(False)
        self.export_json_btn.clicked.connect(self._export_json)
        action_row.addWidget(self.export_json_btn)

        c_layout.addLayout(action_row)

        # Stage list box
        stg_lbl = QLabel("STAGE PROGRESS")
        stg_lbl.setProperty("class", "lattice-section-label")
        c_layout.addWidget(stg_lbl)

        self.stage_list_box = QPlainTextEdit()
        self.stage_list_box.setProperty("class", "code-block")
        self.stage_list_box.setReadOnly(True)
        self.stage_list_box.setMinimumHeight(180)
        c_layout.addWidget(self.stage_list_box)

        # Verification Checklist table
        ver_lbl = QLabel("OPERATIONAL VERIFICATION CHECKLIST")
        ver_lbl.setProperty("class", "lattice-section-label")
        c_layout.addWidget(ver_lbl)

        self.ver_box = QPlainTextEdit()
        self.ver_box.setProperty("class", "code-block")
        self.ver_box.setReadOnly(True)
        self.ver_box.setMinimumHeight(130)
        c_layout.addWidget(self.ver_box)

        layout.addWidget(card)
        layout.addStretch()

        scroll.setWidget(content)
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(scroll)
        return page

    def _run_workflow(self) -> None:
        self.execute_btn.setEnabled(False)
        self.stage_list_box.clear()
        self.ver_box.clear()
        self.export_md_btn.setEnabled(False)
        self.export_json_btn.setEnabled(False)

        target = self._get_endpoint_target()
        env = self._get_vcf_env()
        mon = self._get_monitoring_config()
        mode = self._get_deployment_mode()

        opts = WorkflowOptions(
            deployment_mode=mode,
            dry_run=self.dry_run_check.isChecked(),
            restart_service=(mode == DeploymentMode.PUSH and not self.dry_run_check.isChecked()),
            verify_telemetry=True,
        )

        executor = self._create_executor(target)
        if self.mock_vcf_check.isChecked():
            adapter = MockVCFOpsIntegration(env=env)
        else:
            adapter = get_adapter(env)

        workflow = ConfigureEndpointWorkflow(
            target=target,
            vcf_env=env,
            monitoring=mon,
            executor=executor,
            vcf_adapter=adapter,
            options=opts,
        )

        self.worker_thread = QThread()
        self.worker = WorkflowWorker(workflow)
        self.worker.moveToThread(self.worker_thread)

        self.worker_thread.started.connect(self.worker.run)
        self.worker.stage_updated.connect(self._on_worker_stage)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.failed.connect(self._on_worker_failed)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)

        self.worker_thread.start()

    def _on_worker_stage(self, result: StageResult) -> None:
        line = f"[{result.stage.value}] {result.status.value:<7} {result.message}"
        self.stage_list_box.appendPlainText(line)

    def _on_worker_finished(self, summary: RunSummary) -> None:
        self.last_summary = summary
        self.execute_btn.setEnabled(True)
        self.export_md_btn.setEnabled(True)
        self.export_json_btn.setEnabled(True)

        # Populate verification checklist
        chk = summary.verification
        v_lines = [
            f"Collector Reachable:     {chk.collector_reachable.value}",
            f"Telegraf Installed:      {chk.telegraf_installed.value}",
            f"Config Valid:            {chk.config_valid.value}",
            f"Service Running:         {chk.service_running.value}",
            f"Local Metrics Generated: {chk.local_metrics_generated.value}",
            f"VCF Ops Ingestion:       {chk.vcf_ops_ingestion.value}",
            "",
            f"Overall Status:          {'PASS' if summary.success else 'FAIL'}",
        ]
        self.ver_box.setPlainText("\n".join(v_lines))

    def _on_worker_failed(self, error: str) -> None:
        self.execute_btn.setEnabled(True)
        self.stage_list_box.appendPlainText(f"\nFATAL WORKFLOW ERROR: {error}")
        QMessageBox.critical(self, "Workflow Error", f"Workflow execution failed: {error}")

    def _export_markdown(self) -> None:
        if not self.last_summary:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Markdown Summary", "vcf-telegraf-summary.md", "Markdown (*.md)")
        if path:
            Path(path).write_text(self.last_summary.to_markdown(), encoding="utf-8")
            QMessageBox.information(self, "Export Successful", f"Saved summary to {path}")

    def _export_json(self) -> None:
        if not self.last_summary:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export JSON Summary", "vcf-telegraf-summary.json", "JSON (*.json)")
        if path:
            Path(path).write_text(self.last_summary.to_json(), encoding="utf-8")
            QMessageBox.information(self, "Export Successful", f"Saved summary to {path}")

    # --------------------------------------------------------------------------
    # Helper methods: State & Models
    # --------------------------------------------------------------------------
    def _get_vcf_env(self) -> VCFEnvironment:
        collector_addr = self.vcf_collector_input.text().strip() or "10.10.10.50"
        return VCFEnvironment(
            url=self.vcf_url_input.text().strip() or "https://vcf-ops.local",
            username=self.vcf_user_input.text().strip() or "admin",
            password=self.vcf_pass_input.text().strip() or None,
            token=self.vcf_token_input.text().strip() or None,
            collector=CollectorInfo(address=collector_addr),
            verify_ssl=self.vcf_ssl_check.isChecked(),
        )

    def _get_endpoint_target(self) -> EndpointTarget:
        method_str = self.ep_method_combo.currentText().split()[0].lower()
        if method_str not in ("ssh", "mock", "local", "package"):
            method_str = "ssh"
        return EndpointTarget(
            hostname=self.ep_host_input.text().strip() or "10.10.10.101",
            connection_method=ConnectionMethod(method_str),
            username=self.ep_user_input.text().strip() or "root",
            password=self.ep_pass_input.text().strip() or None,
            key_filename=self.ep_key_input.text().strip() or None,
        )

    def _get_monitoring_config(self) -> MonitoringConfig:
        return MonitoringConfig(
            cpu=CpuInputConfig(enabled=self.cpu_check.isChecked()),
            mem=MemInputConfig(enabled=self.mem_check.isChecked()),
            disk=DiskInputConfig(enabled=self.disk_check.isChecked()),
            net=NetInputConfig(enabled=self.net_check.isChecked()),
            system=SystemInputConfig(enabled=self.sys_check.isChecked()),
            swap=SwapInputConfig(enabled=self.swap_check.isChecked()),
        )

    def _get_deployment_mode(self) -> DeploymentMode:
        if self.mode_push.isChecked():
            return DeploymentMode.PUSH
        if self.mode_script.isChecked():
            return DeploymentMode.SCRIPT
        return DeploymentMode.CONFIG_ONLY

    def _create_executor(self, target: EndpointTarget) -> Any:
        m = target.connection_method.value
        if m == "mock":
            return MockExecutor()
        if m == "local":
            return LocalExecutor()
        if m == "package":
            return PackageExecutor(output_dir="./vcf-telegraf-bundle")
        return SSHExecutor(target=target)

    def _load_saved_state(self) -> None:
        recent_envs = self.state_store.list_environments()
        if recent_envs:
            latest = recent_envs[0]
            self.vcf_url_input.setText(latest.url)
            self.vcf_collector_input.setText(latest.collector.address)
            self.vcf_user_input.setText(latest.username or "")
            self.vcf_ssl_check.setChecked(latest.verify_ssl)

        state = self.state_store.load()
        if state.recent_endpoints:
            self.ep_host_input.setText(state.recent_endpoints[0])
