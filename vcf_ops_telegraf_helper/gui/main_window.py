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
import sys
from typing import Any, Optional

from PySide6.QtCore import QObject, QThread, Signal, Qt
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

from vcf_ops_telegraf_helper.adapters.base import VCFOpsIntegration
from vcf_ops_telegraf_helper.adapters.factory import get_adapter
from vcf_ops_telegraf_helper.executors.base import EndpointExecutor
from vcf_ops_telegraf_helper.executors.local import LocalExecutor
from vcf_ops_telegraf_helper.executors.package import PackageExecutor
from vcf_ops_telegraf_helper.executors.ssh import SSHExecutor
from vcf_ops_telegraf_helper.executors.winrm import WinRMExecutor
from vcf_ops_telegraf_helper.gui.theme import build_stylesheet
from vcf_ops_telegraf_helper.logger import get_log_file_path, get_logger
from vcf_ops_telegraf_helper.models.endpoint import (
    ConnectionMethod,
    EndpointTarget,
    OSFamily,
)
from vcf_ops_telegraf_helper.models.monitoring import (
    ApacheInputConfig,
    CpuInputConfig,
    DiskInputConfig,
    DiskIoInputConfig,
    DockerInputConfig,
    MemInputConfig,
    MonitoringConfig,
    MssqlInputConfig,
    MysqlInputConfig,
    NetInputConfig,
    NginxInputConfig,
    PingInputConfig,
    PostgresqlInputConfig,
    ProcessesInputConfig,
    SwapInputConfig,
    SystemInputConfig,
    WinPerfCountersInputConfig,
    WinServicesInputConfig,
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


class QtProgressReporter:
    """Adapts workflow progress events into Qt signals."""

    def __init__(self, callback: Any) -> None:
        self._callback = callback

    def on_stage_start(self, stage: WorkflowStage) -> None:
        pass

    def on_stage_complete(self, result: StageResult) -> None:
        self._callback(result)

    def on_message(self, message: str) -> None:
        pass


class WorkflowWorker(QObject):
    """Background worker executing the ConfigureEndpointWorkflow to keep Qt event loop responsive."""

    stage_updated = Signal(object)  # StageResult
    finished = Signal(object)  # RunSummary
    failed = Signal(str)

    def __init__(
        self,
        environment: VCFEnvironment,
        target: EndpointTarget,
        monitoring: MonitoringConfig,
        executor: EndpointExecutor,
        adapter: VCFOpsIntegration,
        options: Optional[WorkflowOptions] = None,
    ) -> None:
        super().__init__()
        self.reporter = QtProgressReporter(self.stage_updated.emit)
        self.workflow = ConfigureEndpointWorkflow(
            environment=environment,
            target=target,
            monitoring=monitoring,
            executor=executor,
            adapter=adapter,
            options=options,
            reporter=self.reporter,
        )

    def run(self) -> None:
        try:
            summary = self.workflow.run()
            self.finished.emit(summary)
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    """Main window for the native Lattice-styled administrator helper."""

    def __init__(self, state_store: Optional[StateStore] = None) -> None:
        super().__init__()
        self.state_store = state_store or StateStore()
        self.current_theme = "dark"
        self.last_summary: Optional[RunSummary] = None
        self.worker_thread: Optional[QThread] = None
        self.logger = get_logger("gui")
        self._updating_catalog = False

        self.setWindowTitle("VCF Operations Open Telegraf Helper")
        self.resize(1020, 720)
        self.setMinimumSize(880, 600)

        self._init_ui()
        self._apply_theme()
        self._load_saved_state()
        self.logger.info("MainWindow initialized")

    def _apply_theme(self) -> None:
        self.setStyleSheet(build_stylesheet(self.current_theme))
        if hasattr(self, "theme_btn"):
            self.theme_btn.setText("Theme: Light" if self.current_theme == "dark" else "Theme: Dark")

    def _toggle_theme(self) -> None:
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self._apply_theme()

    def _show_log_dialog(self) -> None:
        log_path = get_log_file_path()
        content = "Log file does not exist yet."
        if log_path.exists():
            try:
                lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                content = "\n".join(lines[-200:])
            except Exception as exc:
                content = f"Error reading log file: {exc}"

        dlg = QMessageBox(self)
        dlg.setWindowTitle("Application Log")
        dlg.setText(f"Log file: {log_path}")
        dlg.setDetailedText(content)
        dlg.exec()

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
        subtitle_label = QLabel("v0.2.0  |  Broadcom Supported Workflow  |  Local Utility")
        subtitle_label.setProperty("class", "lattice-caption")

        header_title_col = QVBoxLayout()
        header_title_col.setSpacing(2)
        header_title_col.addWidget(title_label)
        header_title_col.addWidget(subtitle_label)

        header_layout.addLayout(header_title_col)
        header_layout.addStretch()

        self.log_btn = QPushButton("View Log")
        self.log_btn.clicked.connect(self._show_log_dialog)
        header_layout.addWidget(self.log_btn)

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
            self.logger.info("Validating VCF connection to %s", env.url)
            adapter = get_adapter(env)
            valid = adapter.validate_connection()
            if valid:
                self.logger.info("VCF connection validated successfully to %s", env.url)
                self.vcf_status_label.setText("Status: PASS (Connected)")
                self.vcf_status_label.setStyleSheet("color: #199e70; font-weight: 600;")
                self.state_store.save_environment(env)
            else:
                self.logger.warning("VCF connection refused or unreachable for %s", env.url)
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

        grid.addWidget(QLabel("Target OS:"), 0, 0)
        self.ep_os_combo = QComboBox()
        self.ep_os_combo.addItems(["Linux", "Windows"])
        self.ep_os_combo.currentTextChanged.connect(self._on_os_changed)
        grid.addWidget(self.ep_os_combo, 0, 1)

        grid.addWidget(QLabel("Hostname or IP Address:"), 1, 0)
        self.ep_host_input = QLineEdit("10.10.10.101")
        grid.addWidget(self.ep_host_input, 1, 1)

        grid.addWidget(QLabel("Connection Method:"), 2, 0)
        self.ep_method_combo = QComboBox()
        self.ep_method_combo.addItems(["SSH (Linux Remote)", "WinRM (Windows Remote)", "Local Subprocess", "Package Script Bundle"])
        grid.addWidget(self.ep_method_combo, 2, 1)

        grid.addWidget(QLabel("Port:"), 3, 0)
        self.ep_port_input = QLineEdit("22")
        grid.addWidget(self.ep_port_input, 3, 1)

        grid.addWidget(QLabel("Username:"), 4, 0)
        self.ep_user_input = QLineEdit("root")
        grid.addWidget(self.ep_user_input, 4, 1)

        grid.addWidget(QLabel("Password:"), 5, 0)
        self.ep_pass_input = QLineEdit()
        self.ep_pass_input.setEchoMode(QLineEdit.Password)
        grid.addWidget(self.ep_pass_input, 5, 1)

        self.ep_key_label = QLabel("SSH Key Path:")
        grid.addWidget(self.ep_key_label, 6, 0)
        self.ep_key_input = QLineEdit("~/.ssh/id_rsa")
        grid.addWidget(self.ep_key_input, 6, 1)

        self.ep_auto_install_check = QCheckBox("Install open-source Telegraf agent if missing (InfluxData official distribution)")
        self.ep_auto_install_check.setChecked(True)
        grid.addWidget(self.ep_auto_install_check, 7, 0, 1, 2)

        c_layout.addLayout(grid)

        # Missing agent guidance banner
        self.ep_missing_banner = QFrame()
        self.ep_missing_banner.setProperty("class", "lattice-card")
        self.ep_missing_banner.setStyleSheet(
            "border-left: 4px solid #d97706; background-color: rgba(217, 119, 6, 0.12); padding: 10px; border-radius: 6px;"
        )
        mb_layout = QVBoxLayout(self.ep_missing_banner)
        mb_layout.setContentsMargins(10, 8, 10, 8)
        mb_layout.setSpacing(4)
        mb_title = QLabel("Telegraf Agent Not Detected on Host")
        mb_title.setStyleSheet("font-weight: 600; color: #d97706; font-size: 13px;")
        mb_desc = QLabel(
            "This endpoint does not currently have Telegraf installed. "
            "Keep 'Install open-source Telegraf agent' enabled to automatically download and register "
            "the official InfluxData agent before applying VCF Operations monitoring."
        )
        mb_desc.setProperty("class", "lattice-muted")
        mb_desc.setWordWrap(True)
        mb_layout.addWidget(mb_title)
        mb_layout.addWidget(mb_desc)
        self.ep_missing_banner.setVisible(False)
        c_layout.addWidget(self.ep_missing_banner)

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

    def _on_os_changed(self, os_name: str) -> None:
        if os_name.lower() == "windows":
            self.ep_method_combo.setCurrentText("WinRM (Windows Remote)")
            self.ep_port_input.setText("5985")
            if self.ep_user_input.text() == "root":
                self.ep_user_input.setText("Administrator")
            self.ep_key_input.setEnabled(False)
            self.ep_key_label.setEnabled(False)
            if hasattr(self, "win_perf_check"):
                self.win_perf_check.setChecked(True)
        else:
            self.ep_method_combo.setCurrentText("SSH (Linux Remote)")
            self.ep_port_input.setText("22")
            if self.ep_user_input.text() == "Administrator":
                self.ep_user_input.setText("root")
            self.ep_key_input.setEnabled(True)
            self.ep_key_label.setEnabled(True)
            if hasattr(self, "win_perf_check"):
                self.win_perf_check.setChecked(False)

    def _detect_endpoint(self) -> None:
        self.ep_status_label.setText("Detecting...")
        try:
            target = self._get_endpoint_target()
            self.logger.info("Detecting endpoint %s (%s, OS: %s)", target.hostname, target.connection_method.value, target.os_family.value)
            executor = self._create_executor(target)
            connected = executor.test_connection()
            if not connected:
                self.logger.warning("Endpoint connection test failed for %s", target.hostname)
                self.ep_status_label.setText("Connection failed: unable to connect")
                self.ep_status_label.setStyleSheet("color: #d95926;")
                return

            if target.os_family == OSFamily.WINDOWS:
                os_version = "Microsoft Windows"
                arch = "x86_64"
                installed = False
                version_str = "N/A"
                running = False
                if target.connection_method in (ConnectionMethod.WINRM, ConnectionMethod.LOCAL):
                    if target.connection_method == ConnectionMethod.WINRM or sys.platform == "win32":
                        installed = executor.file_exists("C:\\telegraf\\telegraf.exe")
                        if installed:
                            ver_res = executor.execute("C:\\telegraf\\telegraf.exe version", timeout=10)
                            if ver_res.success:
                                version_str = ver_res.stdout.strip()
                        svc_res = executor.execute("sc.exe query telegraf", timeout=10)
                        running = svc_res.success and "RUNNING" in svc_res.stdout
                        caption_res = executor.execute("(Get-CimInstance Win32_OperatingSystem).Caption", timeout=10)
                        if caption_res.success and caption_res.stdout.strip():
                            os_version = caption_res.stdout.strip().splitlines()[0]

                self.ep_missing_banner.setVisible(not installed)

                self.ep_status_label.setText("Connected & Discovered (Windows)")
                self.ep_status_label.setStyleSheet("color: #199e70; font-weight: 600;")
                details = [
                    f"OS: {os_version}",
                    f"Architecture: {arch}",
                    f"Telegraf Installed: {'YES' if installed else 'NO (auto-install will download InfluxData agent)'}",
                    f"Telegraf Version: {version_str}",
                    f"Service Running: {'YES' if running else 'NO'}",
                    "Config Directory: C:\\telegraf\\telegraf.d",
                    "Agent Distribution: InfluxData Official Open-Source",
                ]
                self.ep_details_box.setPlainText("\n".join(details))
                self.state_store.record_endpoint(target.hostname)
                self.logger.info("Endpoint discovered successfully: %s", target.hostname)
                return

            arch_res = executor.execute("uname -m", timeout=5)
            arch = arch_res.stdout.strip() if arch_res.success else "x86_64"

            os_rel = executor.execute("cat /etc/os-release", timeout=5)
            os_version = "Unknown Linux"
            if os_rel.success and os_rel.stdout.strip():
                for line in os_rel.stdout.splitlines():
                    if line.startswith("PRETTY_NAME="):
                        os_version = line.split("=", 1)[1].strip('"\'')
                        break
                    if line.startswith("NAME=") and os_version == "Unknown Linux":
                        os_version = line.split("=", 1)[1].strip('"\'')
                if os_version == "Unknown Linux" and not any("=" in entry for entry in os_rel.stdout.splitlines()):
                    os_version = os_rel.stdout.strip().splitlines()[0]

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

            self.ep_missing_banner.setVisible(not installed)

            self.ep_status_label.setText("Connected & Discovered")
            self.ep_status_label.setStyleSheet("color: #199e70; font-weight: 600;")

            details = [
                f"OS: {os_version}",
                f"Architecture: {arch}",
                f"Telegraf Installed: {'YES' if installed else 'NO (auto-install will download InfluxData agent)'}",
                f"Telegraf Version: {version_str}",
                f"Service Running: {'YES' if running else 'NO'}",
                "Config Directory: /etc/telegraf/telegraf.d",
                "Agent Distribution: InfluxData Official Open-Source",
            ]
            self.ep_details_box.setPlainText("\n".join(details))
            self.state_store.record_endpoint(target.hostname)
            self.logger.info("Endpoint discovered successfully: %s", target.hostname)
        except Exception as exc:
            self.logger.exception("Endpoint detection exception for %s", self.ep_host_input.text().strip())
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

        sec_in = QLabel("PLUGIN SELECTION & CONFIGURATION")
        sec_in.setProperty("class", "lattice-section-label")
        c_layout.addWidget(sec_in)

        # Preset buttons toolbar
        preset_row = QHBoxLayout()
        preset_lbl = QLabel("PRESETS:")
        preset_lbl.setProperty("class", "lattice-caption")
        preset_row.addWidget(preset_lbl)

        self.btn_preset_baseline = QPushButton("Recommended OS Baseline")
        self.btn_preset_baseline.clicked.connect(self._apply_baseline_preset)
        preset_row.addWidget(self.btn_preset_baseline)

        self.btn_preset_all = QPushButton("Select All")
        self.btn_preset_all.clicked.connect(self._select_all_plugins)
        preset_row.addWidget(self.btn_preset_all)

        self.btn_preset_clear = QPushButton("Clear Workloads")
        self.btn_preset_clear.clicked.connect(self._clear_workload_plugins)
        preset_row.addWidget(self.btn_preset_clear)

        preset_row.addStretch()
        c_layout.addLayout(preset_row)

        # Checkboxes for each plugin
        self.cpu_check = QCheckBox("Enable CPU Metrics")
        self.cpu_check.setChecked(True)

        self.mem_check = QCheckBox("Enable Memory Metrics")
        self.mem_check.setChecked(True)

        self.disk_check = QCheckBox("Enable Disk Usage")
        self.disk_check.setChecked(True)

        self.net_check = QCheckBox("Enable Network Interfaces")
        self.net_check.setChecked(True)

        self.sys_check = QCheckBox("Enable System Load & Uptime")
        self.sys_check.setChecked(True)

        self.swap_check = QCheckBox("Enable Swap Usage")
        self.swap_check.setChecked(True)

        self.diskio_check = QCheckBox("Enable Disk I/O")
        self.diskio_check.setChecked(False)

        self.proc_check = QCheckBox("Enable Process Counts")
        self.proc_check.setChecked(False)

        self.win_perf_check = QCheckBox("Enable Windows Performance Counters")
        self.win_perf_check.setChecked(False)

        self.win_svc_check = QCheckBox("Enable Windows Services")
        self.win_svc_check.setChecked(False)
        self.win_svc_names_input = QLineEdit("*")

        self.nginx_check = QCheckBox("Enable NGINX Monitoring")
        self.nginx_check.setChecked(False)
        self.nginx_url_input = QLineEdit("http://localhost/status")

        self.apache_check = QCheckBox("Enable Apache Monitoring")
        self.apache_check.setChecked(False)
        self.apache_url_input = QLineEdit("http://localhost/server-status?auto")

        self.mysql_check = QCheckBox("Enable MySQL / MariaDB Monitoring")
        self.mysql_check.setChecked(False)
        self.mysql_server_input = QLineEdit("tcp(127.0.0.1:3306)/")

        self.postgres_check = QCheckBox("Enable PostgreSQL Monitoring")
        self.postgres_check.setChecked(False)
        self.postgres_addr_input = QLineEdit("host=localhost user=postgres sslmode=disable")

        self.mssql_check = QCheckBox("Enable Microsoft SQL Server Monitoring")
        self.mssql_check.setChecked(False)
        self.mssql_server_input = QLineEdit("Server=127.0.0.1;Port=1433;User Id=sa;Password=;app name=telegraf;log=1;")

        self.docker_check = QCheckBox("Enable Docker Container Monitoring")
        self.docker_check.setChecked(False)
        self.docker_endpoint_input = QLineEdit("unix:///var/run/docker.sock")

        self.ping_check = QCheckBox("Enable ICMP Ping Reachability")
        self.ping_check.setChecked(False)
        self.ping_url_input = QLineEdit("10.10.10.1")

        self.custom_toml_check = QCheckBox("Enable Custom TOML Injection")
        self.custom_toml_check.setChecked(False)
        self.custom_toml_input = QPlainTextEdit()
        self.custom_toml_input.setProperty("class", "code-block")
        self.custom_toml_input.setPlaceholderText("# Paste custom [[inputs.xyz]] plugin stanzas here...")
        self.custom_toml_input.textChanged.connect(self._on_custom_toml_changed)
        self.custom_toml_check.toggled.connect(
            lambda checked: setattr(self, "_custom_toml_manually_unchecked", not checked if self.custom_toml_input.toPlainText().strip() else False)
        )

        # Catalog definitions
        self.catalog_items = [
            ("cpu", "CPU Metrics", "Core OS", self.cpu_check),
            ("mem", "Memory Metrics", "Core OS", self.mem_check),
            ("disk", "Disk Usage", "Core OS", self.disk_check),
            ("net", "Network Interfaces", "Core OS", self.net_check),
            ("system", "System Load & Uptime", "Core OS", self.sys_check),
            ("swap", "Swap Usage", "Core OS", self.swap_check),
            ("diskio", "Disk I/O", "Core OS", self.diskio_check),
            ("processes", "Process Counts", "Core OS", self.proc_check),
            ("win_perf", "Windows Performance Counters", "Windows", self.win_perf_check),
            ("win_svc", "Windows Services", "Windows", self.win_svc_check),
            ("nginx", "NGINX Web Server", "Workloads", self.nginx_check),
            ("apache", "Apache HTTP Server", "Workloads", self.apache_check),
            ("mysql", "MySQL / MariaDB", "Workloads", self.mysql_check),
            ("postgres", "PostgreSQL Server", "Workloads", self.postgres_check),
            ("mssql", "Microsoft SQL Server", "Workloads", self.mssql_check),
            ("docker", "Docker Containers", "Workloads", self.docker_check),
            ("ping", "ICMP Ping Reachability", "Workloads", self.ping_check),
            ("custom", "Custom TOML Fragment", "Custom", self.custom_toml_check),
        ]

        # Two-pane container
        pane_layout = QHBoxLayout()
        pane_layout.setSpacing(12)

        # Left pane: Catalog list
        left_box = QFrame()
        left_box.setFixedWidth(290)
        left_box.setProperty("class", "lattice-card")
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(6)

        catalog_title = QLabel("AVAILABLE PLUGINS")
        catalog_title.setProperty("class", "lattice-section-label")
        left_layout.addWidget(catalog_title)

        self.plugin_catalog_list = QListWidget()
        self.plugin_catalog_list.setProperty("class", "step-list")
        self.plugin_catalog_list.setMinimumHeight(360)
        left_layout.addWidget(self.plugin_catalog_list)
        pane_layout.addWidget(left_box)

        # Right pane: Config card stack
        right_box = QFrame()
        right_box.setProperty("class", "lattice-card")
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(14, 12, 14, 12)
        right_layout.setSpacing(10)

        self.plugin_config_stack = QStackedWidget()
        right_layout.addWidget(self.plugin_config_stack)
        pane_layout.addWidget(right_box, 1)

        # Build cards for each plugin
        self.plugin_config_stack.addWidget(self._create_plugin_card(
            "CPU Metrics (inputs.cpu)", "Core OS",
            "Collects total and per-cpu usage percentages, system time, and active reporting.",
            self.cpu_check,
            notes="Broadcom standard options applied: percpu = true, totalcpu = true, collect_cpu_time = true, report_active = true"
        ))
        self.plugin_config_stack.addWidget(self._create_plugin_card(
            "Memory Metrics (inputs.mem)", "Core OS",
            "Collects system memory usage, free, used, buffered, and cached memory.",
            self.mem_check,
            notes="Standard Broadcom Linux and Windows memory metric model."
        ))
        self.plugin_config_stack.addWidget(self._create_plugin_card(
            "Disk Usage (inputs.disk)", "Core OS",
            "Monitors disk space utilization across storage mount points, ignoring pseudo filesystems.",
            self.disk_check,
            notes="Auto-ignores: tmpfs, devtmpfs, devfs, iso9660, overlay, aufs, squashfs"
        ))
        self.plugin_config_stack.addWidget(self._create_plugin_card(
            "Network Interface Metrics (inputs.net)", "Core OS",
            "Collects network interface bandwidth, packet counts, drop rates, and errors.",
            self.net_check,
            notes="Collects stats across all active network adapters."
        ))
        self.plugin_config_stack.addWidget(self._create_plugin_card(
            "System Load & Uptime (inputs.system)", "Core OS",
            "Collects 1m, 5m, and 15m load averages and system uptime.",
            self.sys_check,
            notes="Standard system health telemetry."
        ))
        self.plugin_config_stack.addWidget(self._create_plugin_card(
            "Swap Usage (inputs.swap)", "Core OS",
            "Monitors system swap space utilization and in/out paging activity.",
            self.swap_check,
            notes="Tracks swap memory consumption."
        ))
        self.plugin_config_stack.addWidget(self._create_plugin_card(
            "Disk I/O (inputs.diskio)", "Core OS",
            "Tracks read and write byte rates, I/O operations, and queue lengths per storage device.",
            self.diskio_check
        ))
        self.plugin_config_stack.addWidget(self._create_plugin_card(
            "Process Counts (inputs.processes)", "Core OS",
            "Summarizes total processes grouped by status (running, sleeping, stopped, zombie).",
            self.proc_check
        ))
        self.plugin_config_stack.addWidget(self._create_plugin_card(
            "Windows Performance Counters (inputs.win_perf_counters)", "Windows",
            "Collects native Windows Processor, Memory, LogicalDisk, Network Interface, and System counters matching VCF Operations Windows guest OS dashboards.",
            self.win_perf_check,
            notes="Captures Processor (*), Memory, LogicalDisk (*), Network Interface (*), and System objects."
        ))

        # Windows Services Card
        win_svc_widget = QWidget()
        wsw_layout = QVBoxLayout(win_svc_widget)
        wsw_layout.setContentsMargins(0, 0, 0, 0)
        wsw_layout.addWidget(QLabel("Service Names Filter (comma-separated, * for all):"))
        wsw_layout.addWidget(self.win_svc_names_input)
        self.plugin_config_stack.addWidget(self._create_plugin_card(
            "Windows Services Status (inputs.win_services)", "Windows",
            "Monitors status and startup types of Windows services.",
            self.win_svc_check,
            inputs_widget=win_svc_widget
        ))

        # NGINX Card
        nginx_widget = QWidget()
        ng_layout = QVBoxLayout(nginx_widget)
        ng_layout.setContentsMargins(0, 0, 0, 0)
        ng_layout.addWidget(QLabel("NGINX Status URL (stub_status module):"))
        ng_layout.addWidget(self.nginx_url_input)
        self.plugin_config_stack.addWidget(self._create_plugin_card(
            "NGINX Web Server (inputs.nginx)", "Workloads",
            "Scrapes active connections, reading, writing, waiting, and request rates.",
            self.nginx_check,
            inputs_widget=nginx_widget
        ))

        # Apache Card
        apache_widget = QWidget()
        ap_layout = QVBoxLayout(apache_widget)
        ap_layout.setContentsMargins(0, 0, 0, 0)
        ap_layout.addWidget(QLabel("Apache server-status URL:"))
        ap_layout.addWidget(self.apache_url_input)
        self.plugin_config_stack.addWidget(self._create_plugin_card(
            "Apache HTTP Server (inputs.apache)", "Workloads",
            "Collects worker status and request rates from the server-status?auto endpoint.",
            self.apache_check,
            inputs_widget=apache_widget
        ))

        # MySQL Card
        mysql_widget = QWidget()
        my_layout = QVBoxLayout(mysql_widget)
        my_layout.setContentsMargins(0, 0, 0, 0)
        my_layout.addWidget(QLabel("MySQL / MariaDB Connection String:"))
        my_layout.addWidget(self.mysql_server_input)
        self.plugin_config_stack.addWidget(self._create_plugin_card(
            "MySQL / MariaDB (inputs.mysql)", "Workloads",
            "Collects database performance metrics, query counts, and connection pool statistics.",
            self.mysql_check,
            inputs_widget=mysql_widget
        ))

        # PostgreSQL Card
        pg_widget = QWidget()
        pg_layout = QVBoxLayout(pg_widget)
        pg_layout.setContentsMargins(0, 0, 0, 0)
        pg_layout.addWidget(QLabel("PostgreSQL Connection Address:"))
        pg_layout.addWidget(self.postgres_addr_input)
        self.plugin_config_stack.addWidget(self._create_plugin_card(
            "PostgreSQL Server (inputs.postgresql)", "Workloads",
            "Monitors PostgreSQL database statistics, buffer hits, transaction rates, and deadlocks.",
            self.postgres_check,
            inputs_widget=pg_widget
        ))

        # MSSQL Card
        mssql_widget = QWidget()
        ms_layout = QVBoxLayout(mssql_widget)
        ms_layout.setContentsMargins(0, 0, 0, 0)
        ms_layout.addWidget(QLabel("Microsoft SQL Server Connection String:"))
        ms_layout.addWidget(self.mssql_server_input)
        self.plugin_config_stack.addWidget(self._create_plugin_card(
            "Microsoft SQL Server (inputs.sqlserver)", "Workloads",
            "Collects SQL Server engine metrics, batch requests, buffer cache hit ratios, and memory.",
            self.mssql_check,
            inputs_widget=mssql_widget
        ))

        # Docker Card
        docker_widget = QWidget()
        dk_layout = QVBoxLayout(docker_widget)
        dk_layout.setContentsMargins(0, 0, 0, 0)
        dk_layout.addWidget(QLabel("Docker Daemon Socket Endpoint:"))
        dk_layout.addWidget(self.docker_endpoint_input)
        self.plugin_config_stack.addWidget(self._create_plugin_card(
            "Docker Containers (inputs.docker)", "Workloads",
            "Collects container CPU, memory, network, and block I/O statistics.",
            self.docker_check,
            inputs_widget=docker_widget
        ))

        # Ping Card
        ping_widget = QWidget()
        p_layout = QVBoxLayout(ping_widget)
        p_layout.setContentsMargins(0, 0, 0, 0)
        p_layout.addWidget(QLabel("Target URL or IP Address to Ping:"))
        p_layout.addWidget(self.ping_url_input)
        self.plugin_config_stack.addWidget(self._create_plugin_card(
            "ICMP Ping Reachability (inputs.ping)", "Workloads",
            "Measures network reachability, latency, and packet loss to critical gateway or remote endpoints.",
            self.ping_check,
            inputs_widget=ping_widget
        ))

        # Custom TOML Card
        custom_widget = QWidget()
        ct_layout = QVBoxLayout(custom_widget)
        ct_layout.setContentsMargins(0, 0, 0, 0)
        ct_layout.addWidget(QLabel("Custom [[inputs.xyz]] TOML Stanzas:"))
        ct_layout.addWidget(self.custom_toml_input)
        self.plugin_config_stack.addWidget(self._create_plugin_card(
            "Custom TOML Fragment", "Custom",
            "Inject arbitrary input plugin configurations directly into vcf-helper-system.conf.",
            self.custom_toml_check,
            inputs_widget=custom_widget
        ))

        # Populate Left Catalog List and bind synchronization
        for idx, (_, name, cat, chk) in enumerate(self.catalog_items):
            item = QListWidgetItem(f"[{cat}] {name}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if chk.isChecked() else Qt.Unchecked)
            self.plugin_catalog_list.addItem(item)
            chk.toggled.connect(lambda checked, i=idx: self._sync_checkbox_to_catalog(i, checked))

        self.plugin_catalog_list.itemChanged.connect(self._on_catalog_item_changed)
        self.plugin_catalog_list.currentRowChanged.connect(self._on_catalog_row_changed)
        self.plugin_catalog_list.setCurrentRow(0)

        c_layout.addLayout(pane_layout)

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

    def _create_plugin_card(
        self,
        title: str,
        category: str,
        description: str,
        checkbox: QCheckBox,
        inputs_widget: Optional[QWidget] = None,
        notes: Optional[str] = None,
    ) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        header_row = QHBoxLayout()
        cat_badge = QLabel(f"[{category.upper()}]")
        cat_badge.setProperty("class", "lattice-caption")
        cat_badge.setStyleSheet("color: #3987e5; font-weight: 600;")
        header_row.addWidget(cat_badge)

        card_title = QLabel(title)
        card_title.setProperty("class", "lattice-title")
        header_row.addWidget(card_title)
        header_row.addStretch()
        layout.addLayout(header_row)

        layout.addWidget(checkbox)

        desc_lbl = QLabel(description)
        desc_lbl.setProperty("class", "lattice-muted")
        desc_lbl.setWordWrap(True)
        layout.addWidget(desc_lbl)

        if notes:
            notes_lbl = QLabel(notes)
            notes_lbl.setProperty("class", "lattice-caption")
            notes_lbl.setStyleSheet("background: rgba(54, 61, 71, 0.3); padding: 6px; border-radius: 4px;")
            notes_lbl.setWordWrap(True)
            layout.addWidget(notes_lbl)

        if inputs_widget:
            form_group = QFrame()
            form_group.setProperty("class", "lattice-card")
            form_layout = QVBoxLayout(form_group)
            form_layout.setContentsMargins(8, 8, 8, 8)
            form_layout.addWidget(inputs_widget)
            layout.addWidget(form_group)

            inputs_widget.setEnabled(checkbox.isChecked())
            checkbox.toggled.connect(inputs_widget.setEnabled)

        layout.addStretch()
        return card

    def _sync_checkbox_to_catalog(self, row: int, checked: bool) -> None:
        if self._updating_catalog:
            return
        if hasattr(self, "plugin_catalog_list") and 0 <= row < self.plugin_catalog_list.count():
            item = self.plugin_catalog_list.item(row)
            if item:
                new_state = Qt.Checked if checked else Qt.Unchecked
                if item.checkState() != new_state:
                    self._updating_catalog = True
                    try:
                        item.setCheckState(new_state)
                    finally:
                        self._updating_catalog = False

    def _on_catalog_item_changed(self, item: QListWidgetItem) -> None:
        if self._updating_catalog:
            return
        row = self.plugin_catalog_list.row(item)
        if hasattr(self, "catalog_items") and 0 <= row < len(self.catalog_items):
            _, _, _, chk = self.catalog_items[row]
            is_checked = (item.checkState() == Qt.Checked)
            if chk.isChecked() != is_checked:
                self._updating_catalog = True
                try:
                    chk.setChecked(is_checked)
                finally:
                    self._updating_catalog = False

    def _on_catalog_row_changed(self, row: int) -> None:
        if hasattr(self, "plugin_config_stack") and 0 <= row < self.plugin_config_stack.count():
            self.plugin_config_stack.setCurrentIndex(row)

    def _on_custom_toml_changed(self) -> None:
        if hasattr(self, "custom_toml_input") and hasattr(self, "custom_toml_check"):
            has_content = bool(self.custom_toml_input.toPlainText().strip())
            if not has_content:
                self._custom_toml_manually_unchecked = False
                if self.custom_toml_check.isChecked() and not self._updating_catalog:
                    self.custom_toml_check.setChecked(False)
            elif not self.custom_toml_check.isChecked() and not getattr(self, "_custom_toml_manually_unchecked", False) and not self._updating_catalog:
                self.custom_toml_check.setChecked(True)

    def _apply_baseline_preset(self) -> None:
        is_win = (getattr(self, "ep_os_combo", None) and self.ep_os_combo.currentText().lower() == "windows")
        baseline_keys = (
            {"cpu", "mem", "disk", "net", "win_perf", "win_svc"}
            if is_win
            else {"cpu", "mem", "disk", "net", "system", "swap"}
        )
        self._updating_catalog = True
        try:
            for idx, (key, _, _, chk) in enumerate(self.catalog_items):
                want_checked = key in baseline_keys
                chk.setChecked(want_checked)
                item = self.plugin_catalog_list.item(idx)
                if item:
                    item.setCheckState(Qt.Checked if want_checked else Qt.Unchecked)
        finally:
            self._updating_catalog = False

    def _select_all_plugins(self) -> None:
        self._updating_catalog = True
        try:
            for idx, (_, _, _, chk) in enumerate(self.catalog_items):
                chk.setChecked(True)
                item = self.plugin_catalog_list.item(idx)
                if item:
                    item.setCheckState(Qt.Checked)
        finally:
            self._updating_catalog = False

    def _clear_workload_plugins(self) -> None:
        workload_keys = {"nginx", "apache", "mysql", "postgres", "mssql", "docker", "ping", "custom"}
        self._updating_catalog = True
        try:
            for idx, (key, _, _, chk) in enumerate(self.catalog_items):
                if key in workload_keys:
                    chk.setChecked(False)
                    item = self.plugin_catalog_list.item(idx)
                    if item:
                        item.setCheckState(Qt.Unchecked)
        finally:
            self._updating_catalog = False
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

        is_win = target.os_family == OSFamily.WINDOWS
        script_file = "telegraf-utils.ps1" if is_win else "telegraf-utils.sh"
        conf_dir = "C:\\telegraf\\telegraf.d" if is_win else "/etc/telegraf/telegraf.d"
        default_ca = f"{conf_dir}\\ca.pem" if is_win else f"{conf_dir}/ca.pem"
        default_cert = f"{conf_dir}\\cert.pem" if is_win else f"{conf_dir}/cert.pem"
        default_key = f"{conf_dir}\\key.pem" if is_win else f"{conf_dir}/key.pem"

        renderer = TelegrafRenderer()
        sys_toml = renderer.render_system_inputs(mon)
        out_toml = renderer.render_vcf_output(
            collector_address=env.collector.address,
            hostname=target.hostname,
            verify_ssl=env.verify_ssl,
            ca_cert_path=default_ca,
            cert_path=default_cert,
            key_path=default_key,
        )

        active_plugins = []
        if mon.cpu.enabled:
            active_plugins.append("cpu")
        if mon.mem.enabled:
            active_plugins.append("mem")
        if mon.disk.enabled:
            active_plugins.append("disk")
        if mon.net.enabled:
            active_plugins.append("net")
        if mon.system.enabled:
            active_plugins.append("system")
        if mon.swap.enabled:
            active_plugins.append("swap")
        if mon.diskio.enabled:
            active_plugins.append("diskio")
        if mon.processes.enabled:
            active_plugins.append("processes")
        if mon.win_perf_counters.enabled:
            active_plugins.append("win_perf_counters")
        if mon.win_services.enabled:
            active_plugins.append("win_services")
        if mon.nginx.enabled:
            active_plugins.append("nginx")
        if mon.apache.enabled:
            active_plugins.append("apache")
        if mon.mysql.enabled:
            active_plugins.append("mysql")
        if mon.postgresql.enabled:
            active_plugins.append("postgresql")
        if mon.mssql.enabled:
            active_plugins.append("sqlserver")
        if mon.docker.enabled:
            active_plugins.append("docker")
        if mon.ping.enabled:
            active_plugins.append("ping")
        if mon.custom_toml:
            active_plugins.append("custom_toml")

        summary_lines = [
            f"Target: {target.hostname} ({target.connection_method.value}, OS: {target.os_family.value})",
            f"VCF Collector: {env.collector.address} (SSL Verify: {env.verify_ssl})",
            f"Helper Script: https://{env.collector.address}/downloads/salt/{script_file}",
            f"Auto-Install Telegraf: {'YES' if target.install_telegraf else 'NO'}",
            f"Deployment Mode: {mode.value}",
            f"Config Directory: {conf_dir}",
            f"Active Plugins ({len(active_plugins)}): {', '.join(active_plugins)}",
            f"Managed Fragments: {conf_dir}/vcf-helper-system.conf, {conf_dir}/cloudproxy-http.conf",
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
            install_telegraf=target.install_telegraf,
        )

        executor = self._create_executor(target)
        adapter = get_adapter(env)

        self.worker_thread = QThread()
        self.worker = WorkflowWorker(
            environment=env,
            target=target,
            monitoring=mon,
            executor=executor,
            adapter=adapter,
            options=opts,
        )
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
        if method_str not in ("ssh", "winrm", "mock", "local", "package"):
            method_str = "ssh"
        os_str = getattr(self, "ep_os_combo", None)
        os_family = OSFamily.WINDOWS if os_str and os_str.currentText().lower() == "windows" else OSFamily.LINUX
        default_user = "Administrator" if os_family == OSFamily.WINDOWS else "root"
        try:
            port_val = int(self.ep_port_input.text().strip())
        except Exception:
            port_val = 5985 if os_family == OSFamily.WINDOWS else 22
        auto_install = self.ep_auto_install_check.isChecked() if hasattr(self, "ep_auto_install_check") else False

        return EndpointTarget(
            hostname=self.ep_host_input.text().strip() or "10.10.10.101",
            os_family=os_family,
            connection_method=ConnectionMethod(method_str),
            port=port_val,
            username=self.ep_user_input.text().strip() or default_user,
            password=self.ep_pass_input.text().strip() or None,
            key_filename=self.ep_key_input.text().strip() or None,
            winrm_use_ssl=(port_val == 5986),
            install_telegraf=auto_install,
        )

    def _get_monitoring_config(self) -> MonitoringConfig:
        svc_names_raw = self.win_svc_names_input.text().strip() if hasattr(self, "win_svc_names_input") else "*"
        svc_list = [s.strip() for s in svc_names_raw.split(",") if s.strip()] or ["*"]

        nginx_url = self.nginx_url_input.text().strip() if hasattr(self, "nginx_url_input") else "http://localhost/status"
        apache_url = self.apache_url_input.text().strip() if hasattr(self, "apache_url_input") else "http://localhost/server-status?auto"
        mysql_srv = self.mysql_server_input.text().strip() if hasattr(self, "mysql_server_input") else "tcp(127.0.0.1:3306)/"
        pg_addr = self.postgres_addr_input.text().strip() if hasattr(self, "postgres_addr_input") else "host=localhost user=postgres sslmode=disable"
        mssql_srv = self.mssql_server_input.text().strip() if hasattr(self, "mssql_server_input") else "Server=127.0.0.1;Port=1433;User Id=sa;Password=;app name=telegraf;log=1;"
        docker_ep = self.docker_endpoint_input.text().strip() if hasattr(self, "docker_endpoint_input") else "unix:///var/run/docker.sock"
        ping_url = self.ping_url_input.text().strip() if hasattr(self, "ping_url_input") else "10.10.10.1"
        custom_txt = (
            self.custom_toml_input.toPlainText().strip()
            if hasattr(self, "custom_toml_input")
            and (not hasattr(self, "custom_toml_check") or self.custom_toml_check.isChecked())
            else ""
        )

        return MonitoringConfig(
            cpu=CpuInputConfig(enabled=self.cpu_check.isChecked()),
            mem=MemInputConfig(enabled=self.mem_check.isChecked()),
            disk=DiskInputConfig(enabled=self.disk_check.isChecked()),
            net=NetInputConfig(enabled=self.net_check.isChecked()),
            system=SystemInputConfig(enabled=self.sys_check.isChecked()),
            swap=SwapInputConfig(enabled=self.swap_check.isChecked()),
            diskio=DiskIoInputConfig(enabled=bool(getattr(self, "diskio_check", None) and self.diskio_check.isChecked())),
            processes=ProcessesInputConfig(enabled=bool(getattr(self, "proc_check", None) and self.proc_check.isChecked())),
            win_perf_counters=WinPerfCountersInputConfig(enabled=bool(getattr(self, "win_perf_check", None) and self.win_perf_check.isChecked())),
            win_services=WinServicesInputConfig(enabled=bool(getattr(self, "win_svc_check", None) and self.win_svc_check.isChecked()), service_names=svc_list),
            nginx=NginxInputConfig(enabled=bool(getattr(self, "nginx_check", None) and self.nginx_check.isChecked()), urls=[nginx_url]),
            apache=ApacheInputConfig(enabled=bool(getattr(self, "apache_check", None) and self.apache_check.isChecked()), urls=[apache_url]),
            mysql=MysqlInputConfig(enabled=bool(getattr(self, "mysql_check", None) and self.mysql_check.isChecked()), servers=[mysql_srv]),
            postgresql=PostgresqlInputConfig(enabled=bool(getattr(self, "postgres_check", None) and self.postgres_check.isChecked()), address=pg_addr),
            mssql=MssqlInputConfig(enabled=bool(getattr(self, "mssql_check", None) and self.mssql_check.isChecked()), servers=[mssql_srv]),
            docker=DockerInputConfig(enabled=bool(getattr(self, "docker_check", None) and self.docker_check.isChecked()), endpoint=docker_ep),
            ping=PingInputConfig(enabled=bool(getattr(self, "ping_check", None) and self.ping_check.isChecked()), urls=[ping_url]),
            custom_toml=custom_txt,
        )

    def _get_deployment_mode(self) -> DeploymentMode:
        if self.mode_push.isChecked():
            return DeploymentMode.PUSH
        if self.mode_script.isChecked():
            return DeploymentMode.SCRIPT
        return DeploymentMode.CONFIG_ONLY

    def _create_executor(self, target: EndpointTarget) -> Any:
        m = target.connection_method.value
        if m == "local":
            return LocalExecutor()
        if m == "package":
            return PackageExecutor(output_dir="./vcf-telegraf-bundle")
        if m == "winrm":
            return WinRMExecutor(
                hostname=target.hostname,
                port=target.port,
                username=target.username,
                password=target.password,
                use_ssl=target.winrm_use_ssl,
            )
        return SSHExecutor(
            hostname=target.hostname,
            port=target.port,
            username=target.username,
            password=target.password,
            key_filename=target.key_filename,
        )

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
