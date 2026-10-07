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
import shlex
import sys
from typing import Any, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Qt
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vcf_ops_telegraf_helper.adapters.base import VCFOpsIntegration
from vcf_ops_telegraf_helper.adapters.factory import get_adapter
from vcf_ops_telegraf_helper.executors.base import EndpointExecutor
from vcf_ops_telegraf_helper.executors.local import LocalExecutor
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
from vcf_ops_telegraf_helper.models.vcf import (
    CollectorInfo,
    VCFEnvironment,
    VirtualMachineResource,
)
from vcf_ops_telegraf_helper.models.workflow import (
    RunSummary,
    StageResult,
    UninstallOptions,
    WorkflowOptions,
    WorkflowStage,
)
from vcf_ops_telegraf_helper import __version__
from vcf_ops_telegraf_helper.renderer.renderer import TelegrafRenderer
from vcf_ops_telegraf_helper.storage.state import StateStore
from vcf_ops_telegraf_helper.workflow.engine import ConfigureEndpointWorkflow
from vcf_ops_telegraf_helper.workflow.uninstall import UninstallEndpointWorkflow
from vcf_ops_telegraf_helper.workflow.windows import detect_windows_telegraf
from vcf_ops_telegraf_helper.gui.discovery_dialogs import (
    DatabaseConnectDialog,
    DatabaseDiscoveryDialog,
    PerfmonDiscoveryDialog,
    ServicesDiscoveryDialog,
)


class QtProgressReporter:
    """Adapts workflow progress events into Qt signals."""

    def __init__(self, callback: Any, message_callback=None) -> None:
        self._callback = callback
        self._message_callback = message_callback

    def on_stage_start(self, stage: WorkflowStage) -> None:
        self.on_message(f"{stage.value}: starting...")

    def on_stage_complete(self, result: StageResult) -> None:
        self._callback(result)

    def on_message(self, message: str) -> None:
        if self._message_callback:
            self._message_callback(message)


class WorkflowWorker(QObject):
    """Background worker executing the ConfigureEndpointWorkflow to keep Qt event loop responsive."""

    message = Signal(str)
    prepared = Signal(str, str)
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
        self.reporter = QtProgressReporter(self.stage_updated.emit, self.message.emit)
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
            self.workflow.preview_callback = lambda: self.prepared.emit(self.workflow.system_conf_content, self.workflow.vcf_conf_content)
            summary = self.workflow.run()
            self.finished.emit(summary)
        except Exception as exc:
            self.failed.emit(str(exc))


class UninstallWorker(QObject):
    """Background worker executing the UninstallEndpointWorkflow to keep Qt event loop responsive."""

    stage_updated = Signal(object)  # StageResult
    finished = Signal(object)  # UninstallSummary
    failed = Signal(str)

    def __init__(
        self,
        target: EndpointTarget,
        executor: EndpointExecutor,
        options: Optional[UninstallOptions] = None,
    ) -> None:
        super().__init__()
        self.reporter = QtProgressReporter(self.stage_updated.emit)
        self.workflow = UninstallEndpointWorkflow(
            target=target,
            executor=executor,
            reporter=self.reporter,
            options=options,
        )

    def run(self) -> None:
        try:
            summary = self.workflow.run()
            self.finished.emit(summary)
        except Exception as exc:
            self.failed.emit(str(exc))


class AuthSourcesWorker(QObject):
    """Background worker fetching VCF Operations login sources without blocking the UI."""

    finished = Signal(str, object)  # (url, list[str])

    def __init__(self, env: VCFEnvironment) -> None:
        super().__init__()
        self.env = env

    def run(self) -> None:
        try:
            adapter = get_adapter(self.env)
            sources = adapter.list_auth_sources()
        except Exception:
            sources = []
        self.finished.emit(self.env.url, sources)


class MainWindow(QMainWindow):
    """Main window for the native Lattice-styled administrator helper."""

    def __init__(self, state_store: Optional[StateStore] = None) -> None:
        super().__init__()
        self.state_store = state_store or StateStore()
        self.current_theme = "dark"
        self.last_summary: Optional[RunSummary] = None
        self.worker_thread: Optional[QThread] = None
        self.uninstall_worker_thread: Optional[QThread] = None
        self.uninstall_worker: Optional[UninstallWorker] = None
        self.auth_sources_thread: Optional[QThread] = None
        self.auth_sources_worker: Optional[AuthSourcesWorker] = None
        self._loading_auth_sources_url: Optional[str] = None
        self.logger = get_logger("gui")
        self._updating_catalog = False
        self._cached_vms: list[VirtualMachineResource] = []
        self.selected_vm: Optional[VirtualMachineResource] = None
        self.selected_vm_mor: Optional[str] = None
        self.bound_vm: Optional[VirtualMachineResource] = None
        self.selected_vc_id: Optional[str] = None
        self.selected_vm_name: Optional[str] = None
        self.discovered_hostname: Optional[str] = None
        self._vcf_validated = False
        self._validated_url: Optional[str] = None
        self._endpoint_detected = False
        self._current_step = 0
        self._next_buttons: dict[int, QPushButton] = {}

        self.setWindowTitle("VCF Operations Open Telegraf Helper")
        self.resize(1000, 700)
        # Narrow enough for small laptop screens, wide enough that no step needs a sideways scroll
        self.setMinimumSize(980, 640)

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
                content = "\n".join(lines[-250:])
            except Exception as exc:
                content = f"Error reading log file: {exc}"

        dlg = QDialog(self)
        dlg.setWindowTitle("Application Log")
        dlg.resize(900, 600)
        vbox = QVBoxLayout(dlg)
        lbl = QLabel(f"Log file: {log_path} (most recent 250 lines)")
        lbl.setProperty("class", "lattice-section-label")
        vbox.addWidget(lbl)
        edit = QPlainTextEdit(dlg)
        edit.setProperty("class", "code-block")
        edit.setReadOnly(True)
        edit.setPlainText(content)
        vbox.addWidget(edit, 1)
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(dlg.reject)
        vbox.addWidget(btn_box)
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

        title_label = QLabel(f"VCF Operations Open Telegraf Helper v{__version__}")
        title_label.setProperty("class", "lattice-title")
        title_label.setWordWrap(True)

        header_layout.addWidget(title_label)
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
        self.step_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        steps = [
            "1. Connect",
            "2. Select VM",
            "3. Configure Target VM",
            "4. Monitoring Inputs",
            "5. Review & Preview",
            "6. Execute & Verify",
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
        self.page_stack.addWidget(self._build_connect_page())
        self.page_stack.addWidget(self._build_select_vm_page())
        self.page_stack.addWidget(self._build_target_page())
        self._monitoring_page = self._build_monitoring_page()
        self.page_stack.addWidget(self._monitoring_page)
        self.page_stack.addWidget(self._build_review_page())
        self.page_stack.addWidget(self._build_execute_page())

        # Any monitoring input toggle can lock or unlock the steps after it
        for *_, chk in self.catalog_items:
            chk.toggled.connect(lambda _: self._refresh_step_gating())

        body_layout.addWidget(self.page_stack, 1)
        main_layout.addLayout(body_layout, 1)
        self._refresh_step_gating()

    def _on_step_changed(self, row: int) -> None:
        if row < 0:
            return
        reason = self._gate_reason(row) if row > self._current_step else None
        if reason is not None:
            # Locked: stay on the current step
            self.step_list.blockSignals(True)
            self.step_list.setCurrentRow(self._current_step)
            self.step_list.blockSignals(False)
            return
        self._current_step = row
        if row == self.STEP_TARGET:
            self._update_target_summary()
        elif row == self.STEP_REVIEW:
            self._update_preview()
        elif row == self.STEP_EXECUTE:
            self._update_cli_command()
        self.page_stack.setCurrentIndex(row)

    # --------------------------------------------------------------------------
    # Step gating
    # --------------------------------------------------------------------------
    STEP_CONNECT, STEP_SELECT_VM, STEP_TARGET, STEP_MONITORING, STEP_REVIEW, STEP_EXECUTE = range(6)

    def _gate_reason(self, step: int) -> Optional[str]:
        """Return why the given step cannot be entered yet, or None when it is unlocked."""
        if step > self.STEP_CONNECT and not self._vcf_validated:
            return "Validate the VCF Operations connection first."
        if step > self.STEP_SELECT_VM and self.bound_vm is None:
            return "Select a virtual machine first."
        if step > self.STEP_TARGET:
            if self._selected_collector() is None:
                return "Choose a collector or collector group first."
            if not self._endpoint_detected:
                return "Detect the endpoint with the entered credentials first."
        if step > self.STEP_MONITORING and not self._has_monitoring_inputs():
            return "Enable at least one monitoring input first."
        return None

    def _max_unlocked_step(self) -> int:
        step = self.STEP_CONNECT
        while step < self.STEP_EXECUTE and self._gate_reason(step + 1) is None:
            step += 1
        return step

    def _refresh_step_gating(self) -> None:
        if not hasattr(self, "step_list"):
            return
        max_step = self._max_unlocked_step()
        for idx in range(self.step_list.count()):
            item = self.step_list.item(idx)
            flags = item.flags()
            if idx <= max_step:
                item.setFlags(flags | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                item.setToolTip("")
            else:
                item.setFlags(flags & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)
                item.setToolTip(self._gate_reason(idx) or "")
        for idx, btn in getattr(self, "_next_buttons", {}).items():
            reason = self._gate_reason(idx + 1)
            btn.setEnabled(reason is None)
            btn.setToolTip(reason or "")

    def _has_monitoring_inputs(self) -> bool:
        items = getattr(self, "catalog_items", None)
        if not items:
            return True
        return any(chk.isChecked() and chk.isEnabled() for *_, chk in items)

    def _invalidate_vcf_connection(self, *_: Any) -> None:
        if not getattr(self, "_vcf_validated", False):
            return
        self._vcf_validated = False
        if hasattr(self, "vcf_status_label"):
            self.vcf_status_label.setText("Status: Not checked (settings changed)")
            self.vcf_status_label.setStyleSheet("")
        self._refresh_step_gating()

    def _invalidate_endpoint_detection(self, *_: Any) -> None:
        self._endpoint_detected = False
        if hasattr(self, "ep_status_label"):
            self.ep_status_label.setText("Not detected yet")
            self.ep_status_label.setStyleSheet("")
        if hasattr(self, "ep_details_box"):
            self.ep_details_box.setPlainText("Endpoint details will appear here after detection.")
        if hasattr(self, "ep_missing_banner"):
            self.ep_missing_banner.setVisible(False)
        self._refresh_step_gating()

    def _build_nav(self, step: int, back_label: Optional[str], next_label: Optional[str]) -> tuple[QFrame, Optional[QHBoxLayout]]:
        """Bottom navigation bar; the Next button is registered for gating."""
        nav_frame = QFrame()
        nav_frame.setProperty("class", "lattice-card")
        nav_layout = QHBoxLayout(nav_frame)
        nav_layout.setContentsMargins(16, 10, 16, 10)
        # "&" marks a keyboard shortcut in Qt button text; "&&" shows a literal ampersand
        back_label = back_label.replace("&", "&&") if back_label else back_label
        next_label = next_label.replace("&", "&&") if next_label else next_label
        if back_label:
            back_btn = QPushButton(f"<- Back: {back_label}")
            back_btn.clicked.connect(lambda: self.step_list.setCurrentRow(step - 1))
            nav_layout.addWidget(back_btn)
        nav_layout.addStretch()
        if next_label:
            next_btn = QPushButton(f"Next: {next_label} ->")
            next_btn.setProperty("class", "primary")
            next_btn.clicked.connect(lambda: self.step_list.setCurrentRow(step + 1))
            nav_layout.addWidget(next_btn)
            self._next_buttons[step] = next_btn
        return nav_frame, nav_layout

    @staticmethod
    def _wrap_page(content: QWidget, nav_frame: QFrame) -> QWidget:
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(scroll, 1)
        v.addWidget(nav_frame)
        return page

    # --------------------------------------------------------------------------
    # Step 1: Connect to VCF Operations
    # --------------------------------------------------------------------------
    def _build_connect_page(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        card = QFrame()
        card.setProperty("class", "lattice-card")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(12)

        lbl = QLabel("STEP 1: CONNECT TO VCF OPERATIONS")
        lbl.setProperty("class", "lattice-section-label")
        c_layout.addWidget(lbl)

        desc = QLabel(
            "Connect to the VCF Operations 9.1 instance that will monitor the endpoint. "
            "The inventory, cloud proxies, and agent status in the next steps come from this connection."
        )
        desc.setProperty("class", "lattice-muted")
        desc.setWordWrap(True)
        c_layout.addWidget(desc)

        grid = QGridLayout()
        grid.setSpacing(10)

        grid.addWidget(QLabel("VCF Operations URL:"), 0, 0)
        self.vcf_url_input = QLineEdit("https://vcf-ops.local")
        grid.addWidget(self.vcf_url_input, 0, 1)

        self.vcf_auth_type_label = QLabel("Authentication:")
        self.vcf_auth_type_combo = QComboBox()
        self.vcf_auth_type_combo.addItems(["API Token / Key", "Username & Password"])
        self.vcf_auth_type_combo.currentTextChanged.connect(self._on_vcf_auth_type_changed)
        grid.addWidget(self.vcf_auth_type_label, 1, 0)
        grid.addWidget(self.vcf_auth_type_combo, 1, 1)

        self.vcf_token_label = QLabel("API Token / Key:")
        self.vcf_token_input = QLineEdit()
        self.vcf_token_input.setPlaceholderText("Paste VCF Operations API token or service key")
        self.vcf_token_input.setEchoMode(QLineEdit.Password)
        grid.addWidget(self.vcf_token_label, 2, 0)
        grid.addWidget(self.vcf_token_input, 2, 1)

        self.vcf_user_label = QLabel("Username:")
        self.vcf_user_input = QLineEdit()
        grid.addWidget(self.vcf_user_label, 3, 0)
        grid.addWidget(self.vcf_user_input, 3, 1)

        self.vcf_pass_label = QLabel("Password: *")
        self.vcf_pass_input = QLineEdit()
        self.vcf_pass_input.setPlaceholderText("Required for username authentication")
        self.vcf_pass_input.setEchoMode(QLineEdit.Password)
        grid.addWidget(self.vcf_pass_label, 4, 0)
        grid.addWidget(self.vcf_pass_input, 4, 1)

        self.vcf_auth_source_label = QLabel("Login Source:")
        self.vcf_auth_source_combo = QComboBox()
        self.vcf_auth_source_combo.setEditable(True)
        self.vcf_auth_source_combo.addItem("Local")
        self.vcf_auth_source_combo.setToolTip(
            "Where the account lives: Local, or a directory or SSO source configured in VCF Operations"
        )
        grid.addWidget(self.vcf_auth_source_label, 5, 0)
        grid.addWidget(self.vcf_auth_source_combo, 5, 1)

        c_layout.addLayout(grid)
        self._update_vcf_auth_visibility()

        self.vcf_ssl_check = QCheckBox("Verify desktop TLS to VCF Operations")
        self.vcf_ssl_check.setChecked(True)
        self.vcf_ssl_check.toggled.connect(self._on_vcf_ssl_toggled)
        c_layout.addWidget(self.vcf_ssl_check)

        self.agent_ssl_check = QCheckBox("Verify agent TLS to collector")
        self.agent_ssl_check.setChecked(True)
        c_layout.addWidget(self.agent_ssl_check)

        self.vcf_ca_widget = QWidget()
        ca_row = QHBoxLayout(self.vcf_ca_widget)
        ca_row.setContentsMargins(0, 0, 0, 0)
        ca_row.setSpacing(8)
        ca_label = QLabel("Enterprise CA Bundle (optional):")
        self.vcf_ca_input = QLineEdit()
        self.vcf_ca_input.setPlaceholderText("Path to custom CA certificate bundle (e.g. /etc/ssl/certs/lab-ca.pem)")
        self.vcf_ca_browse_btn = QPushButton("Browse...")
        self.vcf_ca_browse_btn.setProperty("class", "secondary")
        self.vcf_ca_browse_btn.clicked.connect(self._browse_ca_cert)
        ca_row.addWidget(ca_label)
        ca_row.addWidget(self.vcf_ca_input, 1)
        ca_row.addWidget(self.vcf_ca_browse_btn)
        c_layout.addWidget(self.vcf_ca_widget)

        test_row = QHBoxLayout()
        self.test_vcf_btn = QPushButton("Validate VCF Connection")
        self.test_vcf_btn.clicked.connect(self._test_vcf_connection)
        test_row.addWidget(self.test_vcf_btn)

        self.vcf_status_label = QLabel("Status: Not checked")
        self.vcf_status_label.setProperty("class", "lattice-caption")
        test_row.addWidget(self.vcf_status_label)
        test_row.addStretch()
        c_layout.addLayout(test_row)

        for w in (self.vcf_url_input, self.vcf_token_input, self.vcf_user_input, self.vcf_pass_input, self.vcf_ca_input):
            w.textChanged.connect(self._invalidate_vcf_connection)
        self.vcf_auth_source_combo.currentTextChanged.connect(self._invalidate_vcf_connection)
        # The instance lists its login sources without authentication, so offer them once the URL is entered
        self.vcf_url_input.editingFinished.connect(self._load_auth_sources)
        self.vcf_auth_type_combo.currentTextChanged.connect(self._invalidate_vcf_connection)
        self.vcf_ssl_check.toggled.connect(self._invalidate_vcf_connection)

        layout.addWidget(card)
        layout.addStretch()

        nav_frame, _ = self._build_nav(self.STEP_CONNECT, None, "Select VM")
        return self._wrap_page(content, nav_frame)

    def _apply_auth_sources(self, url: str, sources: list[str]) -> None:
        if not hasattr(self, "vcf_auth_source_combo"):
            return
        if self.vcf_url_input.text().strip() != url:
            return
        if sources:
            self._auth_sources_url = url
        current = self.vcf_auth_source_combo.currentText()
        self.vcf_auth_source_combo.blockSignals(True)
        self.vcf_auth_source_combo.clear()
        self.vcf_auth_source_combo.addItem("Local")
        for name in sources:
            if name.lower() != "local":
                self.vcf_auth_source_combo.addItem(name)
        if current and current.lower() != "local" and current not in sources:
            self.vcf_auth_source_combo.addItem(current)
        self.vcf_auth_source_combo.setCurrentText(current or "Local")
        self.vcf_auth_source_combo.blockSignals(False)

    def _load_auth_sources(self, sync: bool = False) -> None:
        url = self.vcf_url_input.text().strip()
        if not url or url == getattr(self, "_auth_sources_url", None):
            return
        try:
            env = self._get_vcf_env()
        except Exception:
            return

        if sync:
            try:
                sources = get_adapter(env).list_auth_sources()
            except Exception as exc:
                self.logger.warning("Could not list VCF Operations login sources: %s", exc)
                sources = []
            self._apply_auth_sources(url, sources)
            return

        if getattr(self, "_loading_auth_sources_url", None) == url:
            return
        self._loading_auth_sources_url = url

        self.auth_sources_thread = QThread()
        self.auth_sources_worker = AuthSourcesWorker(env)
        self.auth_sources_worker.moveToThread(self.auth_sources_thread)
        self.auth_sources_thread.started.connect(self.auth_sources_worker.run)
        self.auth_sources_worker.finished.connect(self._on_auth_sources_finished)
        self.auth_sources_worker.finished.connect(self.auth_sources_thread.quit)
        self.auth_sources_worker.finished.connect(self.auth_sources_worker.deleteLater)
        self.auth_sources_thread.finished.connect(self.auth_sources_thread.deleteLater)
        self.auth_sources_thread.start()

    def _on_auth_sources_finished(self, url: str, sources: list[str]) -> None:
        self._loading_auth_sources_url = None
        self._apply_auth_sources(url, sources)

    def _on_vcf_ssl_toggled(self, checked: bool) -> None:
        # A CA bundle only matters when certificates are verified
        if hasattr(self, "vcf_ca_widget"):
            self.vcf_ca_widget.setVisible(checked)

    def _test_vcf_connection(self) -> None:
        self.vcf_status_label.setText("Testing connection...")
        self._vcf_validated = False
        try:
            env = self._get_vcf_env()
            self.logger.info("Validating VCF connection to %s", env.url)
            adapter = get_adapter(env)
            valid = adapter.validate_connection()
            if valid:
                # Reachability alone is not enough: an HTTP 401 still counts as reachable
                adapter.verify_credentials()
                self.logger.info("VCF connection validated successfully to %s", env.url)
                self.vcf_status_label.setText("Status: PASS (Connected)")
                self.vcf_status_label.setStyleSheet("color: #199e70; font-weight: 600;")
                self.state_store.save_environment(env)
                self._vcf_validated = True
                if self._validated_url is not None and self._validated_url != env.url:
                    # A different VCF Operations instance: nothing selected against the old one carries over
                    self._clear_vm_binding()
                self._validated_url = env.url
                self._load_collector_targets(adapter)
                self._fetch_vcf_inventory(adapter)
            else:
                self.logger.warning("VCF connection refused or unreachable for %s", env.url)
                self.vcf_status_label.setText("Status: FAIL (Connection refused or unreachable)")
                self.vcf_status_label.setStyleSheet("color: #d95926; font-weight: 600;")
        except Exception as exc:
            self.vcf_status_label.setText(f"Error: {exc}")
            self.vcf_status_label.setStyleSheet("color: #d95926; font-weight: 600;")
        self._refresh_step_gating()

    # --------------------------------------------------------------------------
    # Step 2: Select VM
    # --------------------------------------------------------------------------
    def _build_select_vm_page(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        card = QFrame()
        card.setProperty("class", "lattice-card")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(12)

        lbl = QLabel("STEP 2: SELECT VIRTUAL MACHINE")
        lbl.setProperty("class", "lattice-section-label")
        c_layout.addWidget(lbl)

        desc = QLabel(
            "Choose the virtual machine to monitor. Guest IP, OS, power state, and agent status are read from "
            "VCF Operations. Templates and VMs no longer present in vCenter are not listed."
        )
        desc.setProperty("class", "lattice-muted")
        desc.setWordWrap(True)
        c_layout.addWidget(desc)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        self.vm_search_input = QLineEdit()
        self.vm_search_input.setPlaceholderText("Filter by name, IP, hostname, or MOR...")
        self.vm_search_input.textChanged.connect(self._filter_vm_table)
        filter_row.addWidget(self.vm_search_input, 2)

        self.vm_os_filter = QComboBox()
        self.vm_os_filter.addItems(["All OS Families", "Windows", "Linux"])
        self.vm_os_filter.currentTextChanged.connect(self._filter_vm_table)
        filter_row.addWidget(self.vm_os_filter, 1)

        self.vm_status_filter = QComboBox()
        self.vm_status_filter.addItems(["All Agent States", "Not installed", "Reporting", "No data"])
        self.vm_status_filter.currentTextChanged.connect(self._filter_vm_table)
        filter_row.addWidget(self.vm_status_filter, 1)

        self.vm_show_off_check = QCheckBox("Show powered-off VMs")
        self.vm_show_off_check.setChecked(False)
        self.vm_show_off_check.toggled.connect(self._filter_vm_table)
        secondary_filter_row = QHBoxLayout()
        secondary_filter_row.addWidget(self.vm_show_off_check)

        self.fetch_vms_btn = QPushButton("Refresh")
        self.fetch_vms_btn.setProperty("class", "secondary")
        self.fetch_vms_btn.clicked.connect(lambda: self._fetch_vcf_inventory())
        secondary_filter_row.addWidget(self.fetch_vms_btn)

        self.vm_count_label = QLabel("0 VMs")
        self.vm_count_label.setProperty("class", "lattice-caption")
        secondary_filter_row.addWidget(self.vm_count_label)

        c_layout.addLayout(filter_row)
        c_layout.addLayout(secondary_filter_row)
        self.vm_status_filter.blockSignals(True)
        self.vm_status_filter.setCurrentText("Reporting")
        self.vm_status_filter.blockSignals(False)

        self.vm_table = QTableWidget()
        self.vm_table.setColumnCount(6)
        self.vm_table.setHorizontalHeaderLabels(["VM Name", "IP Address", "Guest OS", "Power", "VM MOR", "Agent Status"])
        header = self.vm_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 6):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.vm_table.verticalHeader().setVisible(False)
        self.vm_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.vm_table.setSelectionMode(QTableWidget.SingleSelection)
        self.vm_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.vm_table.setMinimumHeight(320)
        self.vm_table.itemSelectionChanged.connect(self._on_vm_row_selected)
        self.vm_table.cellDoubleClicked.connect(self._on_vm_double_clicked)
        c_layout.addWidget(self.vm_table, 1)

        self.vm_inspector_label = QLabel("No VM selected.")
        self.vm_inspector_label.setProperty("class", "lattice-caption")
        self.vm_inspector_label.setWordWrap(True)
        c_layout.addWidget(self.vm_inspector_label)

        layout.addWidget(card)

        nav_frame, _ = self._build_nav(self.STEP_SELECT_VM, "Connect", "Configure Target VM")
        return self._wrap_page(content, nav_frame)

    def _fetch_vcf_inventory(self, adapter: Optional[VCFOpsIntegration] = None) -> None:
        self.fetch_vms_btn.setEnabled(False)
        self.vm_count_label.setText("Querying VCF Operations...")
        try:
            adapter = adapter or get_adapter(self._get_vcf_env())
            self._cached_vms = adapter.list_virtual_machines(strict=True)
            self._populate_vm_table(self._cached_vms)
            self._filter_vm_table()
            self._reconcile_binding()
            if adapter.inventory_warning:
                self.vm_count_label.setText(f"{self.vm_count_label.text()} | {adapter.inventory_warning}")
            self.logger.info("Retrieved %d VMs from VCF Operations inventory", len(self._cached_vms))
        except Exception as exc:
            self._cached_vms = []
            self._populate_vm_table([])
            self._clear_vm_binding()
            self.vm_count_label.setText(f"Query error: {exc}")
            self.logger.warning("Failed to retrieve VM inventory: %s", exc)
        finally:
            self.fetch_vms_btn.setEnabled(True)

    def _reconcile_binding(self) -> None:
        """Keep the bound VM only if it is still in the freshly loaded inventory."""
        if self.bound_vm is None:
            return
        fresh = next((vm for vm in self._cached_vms if vm.resource_id == self.bound_vm.resource_id), None)
        if fresh is None:
            self._clear_vm_binding()
        else:
            self.bound_vm = self.selected_vm = fresh
            self._update_target_summary()

    def _clear_vm_binding(self) -> None:
        self.bound_vm = None
        self.selected_vm = None
        self.selected_vm_mor = None
        self.selected_vc_id = None
        self.selected_vm_name = None
        self.discovered_hostname = None
        if hasattr(self, "vm_inspector_label"):
            self.vm_inspector_label.setText("No VM selected.")
        self._update_target_summary()
        self._invalidate_endpoint_detection()

    @staticmethod
    def _agent_status_text(vm: VirtualMachineResource) -> str:
        if vm.agent_registrations > 1:
            return f"{vm.telegraf_status} ({vm.agent_registrations} registrations)"
        return vm.telegraf_status

    def _populate_vm_table(self, vms: list[VirtualMachineResource]) -> None:
        self.vm_table.setSortingEnabled(False)
        self.vm_table.clearSelection()
        self.vm_table.setRowCount(len(vms))
        for row, vm in enumerate(vms):
            name_item = QTableWidgetItem(vm.name)
            name_item.setData(Qt.UserRole, vm)
            self.vm_table.setItem(row, 0, name_item)
            self.vm_table.setItem(row, 1, QTableWidgetItem(vm.ip_address or "No IP"))
            self.vm_table.setItem(row, 2, QTableWidgetItem(vm.os_name or vm.os_family.capitalize()))
            self.vm_table.setItem(row, 3, QTableWidgetItem(vm.power_state or "Unknown"))
            self.vm_table.setItem(row, 4, QTableWidgetItem(vm.vm_mor or "N/A"))
            st_item = QTableWidgetItem(self._agent_status_text(vm))
            if vm.telegraf_status == "Reporting":
                st_item.setForeground(Qt.darkGreen)
            elif vm.telegraf_status == "No data":
                st_item.setForeground(Qt.darkYellow)
            self.vm_table.setItem(row, 5, st_item)
        self.vm_table.setSortingEnabled(True)
        self.vm_table.sortItems(0, Qt.AscendingOrder)

    def _filter_vm_table(self) -> None:
        if not getattr(self, "_cached_vms", None):
            self.vm_count_label.setText("0 VMs")
            return
        query = self.vm_search_input.text().strip().lower()
        os_filter = self.vm_os_filter.currentText().lower()
        status_filter = self.vm_status_filter.currentText()
        show_off = self.vm_show_off_check.isChecked()

        visible_count = 0
        for row in range(self.vm_table.rowCount()):
            item = self.vm_table.item(row, 0)
            vm = item.data(Qt.UserRole) if item else None
            if not vm:
                continue
            haystack = " ".join(filter(None, (vm.name, vm.ip_address, vm.hostname, vm.vm_mor))).lower()
            show = True
            if query and query not in haystack:
                show = False
            elif "win" in os_filter and vm.os_family.lower() != "windows":
                show = False
            elif "lin" in os_filter and vm.os_family.lower() != "linux":
                show = False
            elif status_filter != "All Agent States" and vm.telegraf_status != status_filter:
                show = False
            elif not show_off and not vm.is_powered_on:
                show = False
            self.vm_table.setRowHidden(row, not show)
            if show:
                visible_count += 1
        self.vm_count_label.setText(f"{visible_count} / {len(self._cached_vms)} VMs")

    def _on_vm_row_selected(self) -> None:
        items = self.vm_table.selectedItems()
        if not items:
            return
        item0 = self.vm_table.item(items[0].row(), 0)
        vm: Optional[VirtualMachineResource] = item0.data(Qt.UserRole) if item0 else None
        if not vm:
            return
        self.selected_vm = vm
        self._bind_vm(vm)

    def _on_vm_double_clicked(self, row: int, col: int) -> None:
        self._on_vm_row_selected()
        if self._gate_reason(self.STEP_TARGET) is None:
            self.step_list.setCurrentRow(self.STEP_TARGET)

    def _bind_vm(self, vm: VirtualMachineResource) -> None:
        """Make the chosen VM the target: identity, connection address, OS, and collector default."""
        changed = self.bound_vm is None or self.bound_vm.resource_id != vm.resource_id
        self.bound_vm = vm
        self.selected_vm_mor = vm.vm_mor
        self.selected_vc_id = vm.vc_id
        self.selected_vm_name = vm.name

        registration = (
            f"registered via {vm.collector_address} ({vm.collector_group})" if vm.collector_address else "no agent registration"
        )
        self.vm_inspector_label.setText(
            f"Selected: {vm.name} | {vm.ip_address or 'No IP'} | {vm.os_name or vm.os_family.capitalize()} | "
            f"{vm.power_state or 'Unknown power state'} | MOR {vm.vm_mor or 'N/A'} | Agent: {self._agent_status_text(vm)}, {registration}"
        )
        if changed:
            self.ep_os_combo.setCurrentText("Windows" if vm.os_family.lower() == "windows" else "Linux")
            self.ep_host_input.setText(vm.ip_address or vm.hostname or vm.name)
            self._preselect_collector(vm)
            self.discovered_hostname = None
            self._invalidate_endpoint_detection()
        self._update_target_summary()
        self.logger.info("Selected VM %s (%s) as target", vm.name, vm.vm_mor)
        self._refresh_step_gating()

    # --------------------------------------------------------------------------
    # Step 3: Configure Target VM
    # --------------------------------------------------------------------------
    def _build_target_page(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        card = QFrame()
        card.setProperty("class", "lattice-card")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(12)

        lbl = QLabel("STEP 3: CONFIGURE TARGET VM")
        lbl.setProperty("class", "lattice-section-label")
        c_layout.addWidget(lbl)

        self.target_summary_label = QLabel("No VM selected.")
        self.target_summary_label.setProperty("class", "lattice-caption")
        self.target_summary_label.setWordWrap(True)
        c_layout.addWidget(self.target_summary_label)

        grid = QGridLayout()
        grid.setSpacing(10)

        grid.addWidget(QLabel("Target OS:"), 0, 0)
        self.ep_os_combo = QComboBox()
        self.ep_os_combo.addItems(["Linux", "Windows"])
        self.ep_os_combo.currentTextChanged.connect(self._on_os_changed)
        grid.addWidget(self.ep_os_combo, 0, 1)

        grid.addWidget(QLabel("Connect To (IP or FQDN):"), 1, 0)
        self.ep_host_input = QLineEdit()
        self.ep_host_input.textChanged.connect(self._on_target_host_changed)
        grid.addWidget(self.ep_host_input, 1, 1)

        self.ep_auth_type_label = QLabel("Authentication:")
        self.ep_auth_radio_widget = QWidget()
        ar_layout = QHBoxLayout(self.ep_auth_radio_widget)
        ar_layout.setContentsMargins(0, 0, 0, 0)
        ar_layout.setSpacing(16)
        self.ep_auth_radio_pass = QRadioButton("Password")
        self.ep_auth_radio_key = QRadioButton("SSH Private Key")
        self.ep_auth_radio_pass.setChecked(True)
        self.ep_auth_group = QButtonGroup(self)
        self.ep_auth_group.addButton(self.ep_auth_radio_pass)
        self.ep_auth_group.addButton(self.ep_auth_radio_key)
        self.ep_auth_radio_pass.toggled.connect(self._on_auth_radio_toggled)
        self.ep_auth_radio_key.toggled.connect(self._on_auth_radio_toggled)
        ar_layout.addWidget(self.ep_auth_radio_pass)
        ar_layout.addWidget(self.ep_auth_radio_key)
        ar_layout.addStretch()
        grid.addWidget(self.ep_auth_type_label, 2, 0)
        grid.addWidget(self.ep_auth_radio_widget, 2, 1)

        self.ep_user_label = QLabel("Username:")
        self.ep_user_input = QLineEdit()
        grid.addWidget(self.ep_user_label, 3, 0)
        grid.addWidget(self.ep_user_input, 3, 1)

        self.ep_pass_label = QLabel("Password:")
        self.ep_pass_input = QLineEdit()
        self.ep_pass_input.setEchoMode(QLineEdit.Password)
        grid.addWidget(self.ep_pass_label, 4, 0)
        credential_row = QHBoxLayout()
        credential_row.addWidget(self.ep_pass_input)
        self.ep_key_browse_btn = QPushButton("Browse...")
        self.ep_key_browse_btn.clicked.connect(self._browse_ssh_key)
        self.ep_key_browse_btn.setVisible(False)
        credential_row.addWidget(self.ep_key_browse_btn)
        grid.addLayout(credential_row, 4, 1)

        self.ep_winrm_ssl_check = QCheckBox("Use HTTPS for WinRM (port 5986)")
        self.ep_winrm_ssl_check.setChecked(False)
        self.ep_winrm_ssl_check.toggled.connect(self._on_winrm_ssl_toggled)
        grid.addWidget(self.ep_winrm_ssl_check, 5, 0, 1, 2)

        grid.addWidget(QLabel("Telegraf Distribution:"), 6, 0)
        self.ep_version_combo = QComboBox()
        self.ep_version_combo.setEditable(True)
        self.ep_version_combo.setMinimumWidth(200)
        self.ep_version_combo.addItems([
            "Auto-install Official 1.40.1 (Latest Stable - Recommended)",
            "Auto-install Official 1.34.0 (1.34 Series)",
            "Auto-install Official 1.32.1 (1.32 Series)",
            "Auto-install Official 1.30.0 (1.30 Series)",
            "Do Not Install (Use Existing Host Agent)",
        ])
        self.ep_version_combo.setCurrentIndex(4)
        if self.ep_version_combo.lineEdit():
            self.ep_version_combo.lineEdit().setCursorPosition(0)
        self.ep_version_combo.currentIndexChanged.connect(self._on_version_combo_changed)
        grid.addWidget(self.ep_version_combo, 6, 1)

        grid.addWidget(QLabel("Collector / Collector Group:"), 7, 0)
        self.ep_collector_combo = QComboBox()
        self.ep_collector_combo.setPlaceholderText("Validate the VCF Operations connection to load cloud proxies")
        self.ep_collector_combo.currentIndexChanged.connect(lambda _: self._refresh_step_gating())
        grid.addWidget(self.ep_collector_combo, 7, 1)

        self.ep_advanced_check = QCheckBox("Show advanced connection options")
        self.ep_advanced_check.setChecked(False)
        self.ep_advanced_check.toggled.connect(self._on_advanced_toggled)
        grid.addWidget(self.ep_advanced_check, 8, 0, 1, 2)

        self.ep_port_label = QLabel("Port:")
        self.ep_port_input = QLineEdit("22")
        self.ep_port_label.setVisible(False)
        self.ep_port_input.setVisible(False)
        grid.addWidget(self.ep_port_label, 9, 0)
        grid.addWidget(self.ep_port_input, 9, 1)

        c_layout.addLayout(grid)
        self._update_auth_and_endpoint_visibility()

        self.ep_missing_banner = QFrame()
        self.ep_missing_banner.setObjectName("epMissingBanner")
        self.ep_missing_banner.setStyleSheet(
            "#epMissingBanner { border-left: 4px solid #d97706; background-color: rgba(217, 119, 6, 0.12); border-radius: 6px; }"
        )
        mb_layout = QVBoxLayout(self.ep_missing_banner)
        mb_layout.setContentsMargins(10, 8, 10, 8)
        mb_layout.setSpacing(4)
        mb_title = QLabel("Telegraf Agent Not Detected on Host")
        mb_title.setStyleSheet("font-weight: 600; color: #d97706; font-size: 13px;")
        mb_desc = QLabel(
            "This endpoint does not currently have Telegraf installed. "
            "Select an 'Auto-install' release family to automatically download and register "
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

        self.ep_uninstall_btn = QPushButton("Uninstall Agent...")
        self.ep_uninstall_btn.setProperty("class", "secondary")
        self.ep_uninstall_btn.clicked.connect(self._on_uninstall_agent_clicked)
        det_row.addWidget(self.ep_uninstall_btn)

        self.ep_status_label = QLabel("Not detected yet")
        self.ep_status_label.setProperty("class", "lattice-caption")
        det_row.addWidget(self.ep_status_label)
        det_row.addStretch()
        c_layout.addLayout(det_row)

        self.ep_details_box = QPlainTextEdit()
        self.ep_details_box.setProperty("class", "code-block")
        self.ep_details_box.setReadOnly(True)
        self.ep_details_box.setMaximumHeight(140)
        self.ep_details_box.setPlainText("Endpoint details will appear here after detection.")
        c_layout.addWidget(self.ep_details_box)

        # Anything that changes how we reach the endpoint invalidates a previous detection
        for w in (self.ep_user_input, self.ep_pass_input, self.ep_port_input):
            w.textChanged.connect(self._invalidate_endpoint_detection)
        self.ep_auth_radio_key.toggled.connect(self._invalidate_endpoint_detection)

        layout.addWidget(card)
        layout.addStretch()

        nav_frame, _ = self._build_nav(self.STEP_TARGET, "Select VM", "Monitoring Inputs")
        return self._wrap_page(content, nav_frame)

    def _update_target_summary(self) -> None:
        vm = self.bound_vm
        if not hasattr(self, "target_summary_label"):
            return
        if vm is None:
            self.target_summary_label.setText("No VM selected. Go back to Step 2 and choose a virtual machine.")
            return
        registration = (
            f"currently registered via {vm.collector_address} ({vm.collector_group})"
            if vm.collector_address
            else "no existing agent registration"
        )
        self.target_summary_label.setText(
            f"Target VM: {vm.name} (MOR {vm.vm_mor or 'N/A'}, vCenter {vm.vc_id or 'N/A'}). "
            f"VCF Operations reports {vm.os_name or 'an unknown guest OS'}, {vm.power_state or 'unknown power state'}, "
            f"agent {self._agent_status_text(vm).lower()}, {registration}."
        )

    def _load_collector_targets(self, adapter: VCFOpsIntegration) -> None:
        previous = self._selected_collector()
        error = None
        try:
            targets = adapter.list_collector_targets()
        except Exception as exc:
            self.logger.warning("Failed to load collector targets: %s", exc)
            error = str(exc)
            targets = []
        self.ep_collector_combo.blockSignals(True)
        self.ep_collector_combo.clear()
        for t in targets:
            if t.is_collector_group:
                label = f"Collector group: {t.name} (virtual IP {t.address})"
            elif t.name:
                label = f"Cloud proxy: {t.display_name or t.address} ({t.address}) in {t.name}"
            else:
                label = f"Cloud proxy: {t.display_name or t.address} ({t.address})"
            self.ep_collector_combo.addItem(label, t)
        restore = -1
        if previous is not None:
            restore = next(
                (
                    i for i in range(self.ep_collector_combo.count())
                    if self.ep_collector_combo.itemData(i).model_dump() == previous.model_dump()
                ),
                -1,
            )
        self.ep_collector_combo.setCurrentIndex(restore if restore >= 0 else (0 if targets else -1))
        self.ep_collector_combo.blockSignals(False)
        if error:
            self.ep_collector_combo.setPlaceholderText(f"Could not load cloud proxies: {error}")
        elif not targets:
            self.ep_collector_combo.setPlaceholderText("No cloud proxies found in VCF Operations")
        if restore < 0 and self.bound_vm is not None:
            self._preselect_collector(self.bound_vm)
        self._refresh_step_gating()

    def _preselect_collector(self, vm: VirtualMachineResource) -> None:
        """Default to where the VM's agent already reports: its group's virtual IP, else that proxy."""
        if not vm.collector_address or not hasattr(self, "ep_collector_combo"):
            return
        group_idx = proxy_idx = -1
        for idx in range(self.ep_collector_combo.count()):
            t: CollectorInfo = self.ep_collector_combo.itemData(idx)
            if t.is_collector_group and t.name == vm.collector_group:
                group_idx = idx
            elif not t.is_collector_group and t.address == vm.collector_address:
                proxy_idx = idx
        idx = group_idx if group_idx >= 0 else proxy_idx
        if idx >= 0:
            self.ep_collector_combo.setCurrentIndex(idx)

    def _selected_collector(self) -> Optional[CollectorInfo]:
        if not hasattr(self, "ep_collector_combo") or self.ep_collector_combo.currentIndex() < 0:
            return None
        return self.ep_collector_combo.currentData()

    def _on_winrm_ssl_toggled(self, checked: bool) -> None:
        if self.ep_port_input.text().strip() in ("", "5985", "5986"):
            self.ep_port_input.setText("5986" if checked else "5985")
        self._invalidate_endpoint_detection()

    def _update_auth_and_endpoint_visibility(self) -> None:
        if not hasattr(self, "ep_os_combo"):
            return
        is_win = self.ep_os_combo.currentText().lower().startswith("win")

        if hasattr(self, "ep_key_browse_btn"):
            self.ep_key_browse_btn.setVisible(not is_win and self.ep_auth_radio_key.isChecked())
        if hasattr(self, "ep_winrm_ssl_check"):
            self.ep_winrm_ssl_check.setVisible(is_win)
        if is_win:
            if hasattr(self, "ep_auth_type_label"):
                self.ep_auth_type_label.setVisible(False)
            if hasattr(self, "ep_auth_radio_widget"):
                self.ep_auth_radio_widget.setVisible(False)
            if hasattr(self, "ep_pass_label"):
                self.ep_pass_label.setText("Password:")
            if hasattr(self, "ep_pass_input"):
                self.ep_pass_input.setEchoMode(QLineEdit.Password)
                self.ep_pass_input.setPlaceholderText("Enter WinRM password")
            if hasattr(self, "ep_user_label"):
                self.ep_user_label.setVisible(True)
            if hasattr(self, "ep_user_input"):
                self.ep_user_input.setVisible(True)
        else:
            if hasattr(self, "ep_auth_type_label"):
                self.ep_auth_type_label.setVisible(True)
            if hasattr(self, "ep_auth_radio_widget"):
                self.ep_auth_radio_widget.setVisible(True)
            use_key = hasattr(self, "ep_auth_radio_key") and self.ep_auth_radio_key.isChecked()
            if hasattr(self, "ep_pass_label"):
                self.ep_pass_label.setText("SSH Key Path:" if use_key else "Password:")
            if hasattr(self, "ep_pass_input"):
                self.ep_pass_input.setEchoMode(QLineEdit.Normal if use_key else QLineEdit.Password)
                self.ep_pass_input.setPlaceholderText("~/.ssh/id_rsa" if use_key else "Enter SSH password")
            if hasattr(self, "ep_user_label"):
                self.ep_user_label.setVisible(True)
            if hasattr(self, "ep_user_input"):
                self.ep_user_input.setVisible(True)

    def _on_auth_radio_toggled(self) -> None:
        if hasattr(self, "ep_pass_input"):
            self.ep_pass_input.clear()
        self._update_auth_and_endpoint_visibility()

    def _update_vcf_auth_visibility(self) -> None:
        if not hasattr(self, "vcf_auth_type_combo"):
            return
        use_key = "key" in self.vcf_auth_type_combo.currentText().lower() or "token" in self.vcf_auth_type_combo.currentText().lower()
        self.vcf_token_label.setVisible(use_key)
        self.vcf_token_input.setVisible(use_key)
        self.vcf_user_label.setVisible(not use_key)
        self.vcf_user_input.setVisible(not use_key)
        self.vcf_pass_label.setVisible(not use_key)
        self.vcf_pass_input.setVisible(not use_key)
        if hasattr(self, "vcf_auth_source_combo"):
            self.vcf_auth_source_label.setVisible(not use_key)
            self.vcf_auth_source_combo.setVisible(not use_key)

    def _on_vcf_auth_type_changed(self, text: str) -> None:
        self._update_vcf_auth_visibility()
        if hasattr(self, "state_store"):
            self.state_store.save_preference("vcf_auth_mode", text)

    def _on_target_host_changed(self, text: str) -> None:
        if hasattr(self, "ep_pass_input"):
            self.ep_pass_input.clear()
            self.ep_user_input.clear()
        self.discovered_hostname = None
        self.detected_config_dir = None
        self.last_summary = None
        if hasattr(self, "stage_list_box"):
            self.stage_list_box.clear()
            self.result_banner.clear()
            self.export_md_btn.setEnabled(False)
            self.export_json_btn.setEnabled(False)
        # The VM identity comes from Step 2; the address only changes how we reach it
        self._invalidate_endpoint_detection()

    def _on_os_changed(self, os_name: str) -> None:
        self._invalidate_endpoint_detection()
        is_win = os_name.lower().startswith("win")
        if is_win:
            if not self.ep_advanced_check.isChecked() or self.ep_port_input.text() == "22":
                self.ep_port_input.setText("5986" if self.ep_winrm_ssl_check.isChecked() else "5985")
            if hasattr(self, "docker_endpoint_input") and self.docker_endpoint_input.text().strip() in ("", "unix:///var/run/docker.sock"):
                self.docker_endpoint_input.setText("npipe:////./pipe/docker_engine")
        else:
            if not self.ep_advanced_check.isChecked() or self.ep_port_input.text() in ("5985", "5986"):
                self.ep_port_input.setText("22")
            if hasattr(self, "docker_endpoint_input") and self.docker_endpoint_input.text().strip() in ("", "npipe:////./pipe/docker_engine"):
                self.docker_endpoint_input.setText("unix:///var/run/docker.sock")

        self._update_auth_and_endpoint_visibility()

        if hasattr(self, "catalog_items"):
            self._update_catalog_os_compatibility(is_win)
            self._apply_baseline_preset()

    def _on_auth_type_changed(self, text: str) -> None:
        self._update_auth_and_endpoint_visibility()

    def _on_advanced_toggled(self, checked: bool) -> None:
        self.ep_port_label.setVisible(checked)
        self.ep_port_input.setVisible(checked)

    def _on_auto_install_toggled(self, checked: bool) -> None:
        if hasattr(self, "ep_version_label"):
            self.ep_version_label.setEnabled(checked)
        if hasattr(self, "ep_version_combo"):
            self.ep_version_combo.setEnabled(checked)

    def _on_version_combo_changed(self) -> None:
        if hasattr(self, "ep_version_combo") and self.ep_version_combo.lineEdit():
            self.ep_version_combo.lineEdit().setCursorPosition(0)

    def _get_selected_telegraf_version(self) -> str:
        if not hasattr(self, "ep_version_combo"):
            return "1.40.1"
        text = self.ep_version_combo.currentText().strip()
        if text.startswith("Do Not Install"):
            return "1.40.1"
        if text.startswith("Auto-install Official "):
            text = text[len("Auto-install Official "):].strip()
        if " " in text:
            text = text.split(" ", 1)[0].strip()
        if text.startswith("v") and len(text) > 1 and text[1].isdigit():
            text = text[1:]
        return text or "1.40.1"

    def _on_uninstall_agent_clicked(self) -> None:
        if self.uninstall_worker_thread and self.uninstall_worker_thread.isRunning():
            return

        target = self._get_endpoint_target()
        is_win = target.os_family == OSFamily.WINDOWS
        cfg_loc = "C:\\telegraf" if is_win else "/etc/telegraf"
        bin_desc = "C:\\telegraf binaries and Windows service" if is_win else "Telegraf package binaries and InfluxData repositories"
        reply = QMessageBox.question(
            self,
            "Confirm Telegraf Uninstallation",
            f"Are you sure you want to completely uninstall Telegraf from {target.hostname}?\n\n"
            "This will:\n"
            f"* Stop and disable the Telegraf service\n"
            f"* Remove {cfg_loc} configuration fragments and certificates\n"
            f"* Purge {bin_desc}\n\n"
            "This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.ep_uninstall_btn.setEnabled(False)
        self.ep_status_label.setText("Uninstalling Telegraf...")
        self.ep_details_box.setPlainText("Initiating uninstallation workflow...")

        try:
            executor = self._create_executor(target)
            opts = UninstallOptions(purge_packages=True, purge_repositories=True)
            self.uninstall_worker_thread = QThread()
            self.uninstall_worker = UninstallWorker(
                target=target,
                executor=executor,
                options=opts,
            )
            self.uninstall_worker.moveToThread(self.uninstall_worker_thread)
            self.uninstall_worker_thread.started.connect(self.uninstall_worker.run)
            self.uninstall_worker.stage_updated.connect(self._on_uninstall_stage_updated)
            self.uninstall_worker.finished.connect(self._on_uninstall_finished)
            self.uninstall_worker.failed.connect(self._on_uninstall_failed)
            self.uninstall_worker.finished.connect(self.uninstall_worker_thread.quit)
            self.uninstall_worker.failed.connect(self.uninstall_worker_thread.quit)
            self.uninstall_worker_thread.start()
        except Exception as exc:
            self.ep_uninstall_btn.setEnabled(True)
            self.ep_status_label.setText(f"Uninstall error: {exc}")
            self.ep_details_box.appendPlainText(f"ERROR: {exc}")
            QMessageBox.critical(self, "Uninstall Failed", f"Failed to execute uninstallation: {exc}")

    def _on_uninstall_stage_updated(self, stage_or_res: Any) -> None:
        if isinstance(stage_or_res, StageResult):
            st_name = stage_or_res.stage.value if hasattr(stage_or_res.stage, "value") else str(stage_or_res.stage)
            self.ep_details_box.appendPlainText(f"[{st_name}] {stage_or_res.status.value}: {stage_or_res.message}")
        elif hasattr(stage_or_res, "value"):
            self.ep_status_label.setText(f"Uninstalling: {stage_or_res.value}")

    def _on_uninstall_finished(self, summary: Any) -> None:
        self.ep_uninstall_btn.setEnabled(True)
        target = self._get_endpoint_target()
        if summary.success:
            self.ep_status_label.setText("Telegraf completely uninstalled")
            if hasattr(self, "ep_missing_banner"):
                self.ep_missing_banner.setVisible(True)
            QMessageBox.information(
                self,
                "Uninstall Complete",
                f"Telegraf has been cleanly removed from {target.hostname}.",
            )
        else:
            self.ep_status_label.setText("Uninstallation completed with warnings")
            QMessageBox.warning(
                self,
                "Uninstall Warning",
                "Uninstallation completed with warnings or leftover artifacts. Check details box.",
            )

    def _on_uninstall_failed(self, error_str: str) -> None:
        self.ep_uninstall_btn.setEnabled(True)
        self.ep_status_label.setText(f"Uninstall error: {error_str}")
        self.ep_details_box.appendPlainText(f"ERROR: {error_str}")
        QMessageBox.critical(self, "Uninstall Failed", f"Failed to execute uninstallation: {error_str}")

    def _detect_endpoint(self) -> None:
        self._endpoint_detected = False
        try:
            self._run_endpoint_detection()
        finally:
            self._refresh_step_gating()

    def _run_endpoint_detection(self) -> None:
        self.ep_status_label.setText("Detecting...")
        if hasattr(self, "ep_missing_banner"):
            self.ep_missing_banner.setVisible(False)
        try:
            target = self._get_endpoint_target()
            if target.connection_method in (ConnectionMethod.SSH, ConnectionMethod.WINRM) and not target.username:
                raise ValueError("Enter an endpoint username before detecting or applying.")
            self.logger.info("Detecting endpoint %s (%s, OS: %s)", target.hostname, target.connection_method.value, target.os_family.value)
            executor = self._create_executor(target)
            connected = executor.test_connection()
            if not connected:
                self.logger.warning("Endpoint connection test failed for %s", target.hostname)
                self.ep_status_label.setText(f"Connection failed: {getattr(executor, 'connection_error', 'unable to connect')}")
                self.ep_status_label.setStyleSheet("color: #d95926;")
                if hasattr(self, "ep_missing_banner"):
                    self.ep_missing_banner.setVisible(False)
                return

            if target.os_family == OSFamily.WINDOWS:
                os_version = "Microsoft Windows"
                arch = "x86_64"
                installed = False
                version_str = "N/A"
                running = False
                binary_path = None
                service_name = None
                if target.connection_method in (ConnectionMethod.WINRM, ConnectionMethod.LOCAL):
                    if target.connection_method == ConnectionMethod.WINRM or sys.platform == "win32":
                        win_det = detect_windows_telegraf(executor)
                        if (win_det.service_name or "").lower() == "ucp-telegraf" or "ucp-telegraf" in (win_det.binary_path or "").lower():
                            raise RuntimeError("VCF Operations owns this ucp-telegraf agent. Manage it through Ops.")
                        self.detected_config_dir = win_det.config_dir
                        installed = win_det.installed
                        binary_path = win_det.binary_path
                        service_name = win_det.service_name
                        version_str = win_det.version or "N/A"
                        running = win_det.running
                        caption_res = executor.execute("(Get-CimInstance Win32_OperatingSystem).Caption", timeout=10)
                        if caption_res.success and caption_res.stdout.strip():
                            os_version = caption_res.stdout.strip().splitlines()[0]
                        h_res = executor.execute("$env:COMPUTERNAME", timeout=10)
                        if h_res.success and h_res.stdout.strip():
                            lines = [ln.strip() for ln in h_res.stdout.splitlines() if ln.strip()]
                            self.discovered_hostname = lines[-1].split(".")[0] if lines else target.hostname
                        else:
                            self.discovered_hostname = target.hostname

                self.ep_missing_banner.setVisible(not installed)

                self._endpoint_detected = True
                self.ep_status_label.setText("Connected & Discovered (Windows)")
                self.ep_status_label.setStyleSheet("color: #199e70; font-weight: 600;")
                auto_install = "Do Not Install" not in self.ep_version_combo.currentText()
                if installed:
                    inst_str = "YES"
                elif auto_install:
                    inst_str = "NO (auto-install will download InfluxData agent)"
                else:
                    inst_str = "NO (auto-install disabled)"

                disc_name = getattr(self, "discovered_hostname", target.hostname)
                details = [
                    f"OS: {os_version}",
                    f"Discovered Hostname: {disc_name}",
                    f"Architecture: {arch}",
                    f"Telegraf Installed: {inst_str}",
                    f"Telegraf Version: {version_str}",
                    f"Service Running: {'YES' if running else 'NO'}",
                ]
                if binary_path:
                    details.append(f"Telegraf Binary: {binary_path}")
                if service_name:
                    details.append(f"Service Name: {service_name}")
                details.extend([
                    f"Config Directory: {self.detected_config_dir}",
                    "Agent Distribution: InfluxData Official Open-Source",
                ])
                self.ep_details_box.setPlainText("\n".join(details))
                self.state_store.record_endpoint(target.hostname)
                self.logger.info("Endpoint discovered successfully: %s (hostname: %s)", target.hostname, disc_name)
                if hasattr(self, "catalog_items"):
                    self._update_catalog_os_compatibility(True)
                    if getattr(self, "_last_detected_os", None) != "windows":
                        self._last_detected_os = "windows"
                        self._apply_baseline_preset()
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

            h_res = executor.execute("hostname -s", timeout=5)
            if h_res.success and h_res.stdout.strip():
                lines = [ln.strip() for ln in h_res.stdout.splitlines() if ln.strip()]
                self.discovered_hostname = lines[-1].split(".")[0] if lines else target.hostname
            else:
                self.discovered_hostname = target.hostname

            self.ep_missing_banner.setVisible(not installed)

            self._endpoint_detected = True
            self.ep_status_label.setText("Connected & Discovered")
            self.ep_status_label.setStyleSheet("color: #199e70; font-weight: 600;")

            auto_install = "Do Not Install" not in self.ep_version_combo.currentText()
            if installed:
                inst_str = "YES"
            elif auto_install:
                inst_str = "NO (auto-install will download InfluxData agent)"
            else:
                inst_str = "NO (auto-install disabled)"

            disc_name = getattr(self, "discovered_hostname", target.hostname)
            details = [
                f"OS: {os_version}",
                f"Discovered Hostname: {disc_name}",
                f"Architecture: {arch}",
                f"Telegraf Installed: {inst_str}",
                f"Telegraf Version: {version_str}",
                f"Service Running: {'YES' if running else 'NO'}",
                "Config Directory: /etc/telegraf/telegraf.d",
                "Agent Distribution: InfluxData Official Open-Source",
            ]
            self.ep_details_box.setPlainText("\n".join(details))
            self.state_store.record_endpoint(target.hostname)
            self.logger.info("Endpoint discovered successfully: %s (hostname: %s)", target.hostname, disc_name)
            if hasattr(self, "catalog_items"):
                self._update_catalog_os_compatibility(False)
                if getattr(self, "_last_detected_os", None) != "linux":
                    self._last_detected_os = "linux"
                    self._apply_baseline_preset()
        except Exception as exc:
            self.logger.exception("Endpoint detection exception for %s", self.ep_host_input.text().strip())
            self.ep_status_label.setText(f"Detection error: {exc}")
            self.ep_status_label.setStyleSheet("color: #d95926;")
            if hasattr(self, "ep_missing_banner"):
                self.ep_missing_banner.setVisible(False)

    # --------------------------------------------------------------------------
    # Step 3: Monitoring Inputs
    # --------------------------------------------------------------------------
    def _build_monitoring_page(self) -> QWidget:
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

        lbl = QLabel("STEP 4: MONITORING INPUTS")
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

        self.sys_check = QCheckBox("Enable System Load && Uptime")
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
        self.win_svc_names_input = QLineEdit("telegraf")

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
        pane_layout = QVBoxLayout()
        pane_layout.setSpacing(12)

        # Left pane: Catalog list
        left_box = QFrame()
        left_box.setProperty("class", "lattice-card")
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(6)

        catalog_title = QLabel("AVAILABLE PLUGINS")
        catalog_title.setProperty("class", "lattice-section-label")
        left_layout.addWidget(catalog_title)

        self.plugin_catalog_list = QListWidget()
        self.plugin_catalog_list.setProperty("class", "step-list")
        self.plugin_catalog_list.setFixedHeight(180)
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
        # Windows Performance Counters Card
        win_perf_widget = QWidget()
        wp_layout = QVBoxLayout(win_perf_widget)
        wp_layout.setContentsMargins(0, 0, 0, 0)
        self.btn_browse_perfmon = QPushButton("⚡ Browse Perfmon Counters")
        self.btn_browse_perfmon.setProperty("class", "secondary")
        self.btn_browse_perfmon.setToolTip("Query installed Windows Performance Counter sets from target endpoint")
        self.btn_browse_perfmon.clicked.connect(self._on_browse_perfmon_clicked)
        wp_layout.addWidget(self.btn_browse_perfmon)
        self.plugin_config_stack.addWidget(self._create_plugin_card(
            "Windows Performance Counters (inputs.win_perf_counters)", "Windows",
            "Collects native Windows Processor, Memory, LogicalDisk, Network Interface, and System counters matching VCF Operations Windows guest OS dashboards.",
            self.win_perf_check,
            inputs_widget=win_perf_widget,
            notes="Captures Processor (*), Memory, LogicalDisk (*), Network Interface (*), and System objects."
        ))

        # Windows Services Card
        win_svc_widget = QWidget()
        wsw_layout = QVBoxLayout(win_svc_widget)
        wsw_layout.setContentsMargins(0, 0, 0, 0)
        wsw_layout.addWidget(QLabel("Service Names Filter (comma-separated, * for all):"))
        wsw_layout.addWidget(self.win_svc_names_input)
        self.btn_discover_services = QPushButton("⚡ Discover Host Services")
        self.btn_discover_services.setProperty("class", "secondary")
        self.btn_discover_services.setToolTip("Query active running services from endpoint via WinRM or SSH")
        self.btn_discover_services.clicked.connect(self._on_discover_services_clicked)
        wsw_layout.addWidget(self.btn_discover_services)
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
        self.btn_discover_mysql = QPushButton("⚡ Connect && Discover DBs")
        self.btn_discover_mysql.setProperty("class", "secondary")
        self.btn_discover_mysql.setToolTip("Query active databases on MySQL server instance")
        self.btn_discover_mysql.clicked.connect(lambda: self._on_discover_databases_clicked("mysql"))
        my_layout.addWidget(self.btn_discover_mysql)
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
        self.btn_discover_pg = QPushButton("⚡ Connect && Discover DBs")
        self.btn_discover_pg.setProperty("class", "secondary")
        self.btn_discover_pg.setToolTip("Query active databases on PostgreSQL server instance")
        self.btn_discover_pg.clicked.connect(lambda: self._on_discover_databases_clicked("postgresql"))
        pg_layout.addWidget(self.btn_discover_pg)
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
        self.btn_discover_mssql = QPushButton("⚡ Connect && Discover DBs")
        self.btn_discover_mssql.setProperty("class", "secondary")
        self.btn_discover_mssql.setToolTip("Query active database catalogs on Microsoft SQL Server instance")
        self.btn_discover_mssql.clicked.connect(lambda: self._on_discover_databases_clicked("mssql"))
        ms_layout.addWidget(self.btn_discover_mssql)
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

        # Long names shorten with an ellipsis (full name in the tooltip) instead of scrolling sideways
        self.plugin_catalog_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.plugin_catalog_list.setTextElideMode(Qt.ElideRight)
        for i in range(self.plugin_catalog_list.count()):
            item = self.plugin_catalog_list.item(i)
            item.setToolTip(item.text())

        self.plugin_catalog_list.itemChanged.connect(self._on_catalog_item_changed)
        self.plugin_catalog_list.currentRowChanged.connect(self._on_catalog_row_changed)
        self.plugin_catalog_list.setCurrentRow(0)

        is_win = (
            self.ep_os_combo.currentText().strip().lower().startswith("win")
            if hasattr(self, "ep_os_combo")
            else False
        )
        self._update_catalog_os_compatibility(is_win)
        self._apply_baseline_preset()
        if is_win and self.docker_endpoint_input.text().strip() in ("", "unix:///var/run/docker.sock"):
            self.docker_endpoint_input.setText("npipe:////./pipe/docker_engine")

        c_layout.addLayout(pane_layout)

        layout.addWidget(card)
        layout.addStretch()

        scroll.setWidget(content)

        nav_frame, _ = self._build_nav(self.STEP_MONITORING, "Configure Target VM", "Review & Preview")

        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(scroll, 1)
        v.addWidget(nav_frame)
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
        card_title.setWordWrap(True)
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

    def _browse_ssh_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select SSH private key")
        if path:
            self.ep_pass_input.setText(path)

    def _browse_ca_cert(self) -> None:
        init_file = (
            self.vcf_ca_input.text().strip()
            if hasattr(self, "vcf_ca_input") and self.vcf_ca_input.text().strip()
            else ""
        )
        selected_file, _ = QFileDialog.getOpenFileName(
            self,
            "Select Enterprise CA Certificate",
            init_file,
            "Certificate Files (*.pem *.crt *.cer);;All Files (*)",
        )
        if selected_file:
            self.vcf_ca_input.setText(selected_file)

    def _apply_baseline_preset(self) -> None:
        is_win = bool(
            getattr(self, "ep_os_combo", None)
            and self.ep_os_combo.currentText().strip().lower().startswith("win")
        )
        baseline_keys = (
            {"win_perf", "win_svc"}
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

    def _update_catalog_os_compatibility(self, is_win: bool) -> None:
        self._updating_catalog = True
        try:
            for idx, (key, _, _, chk) in enumerate(self.catalog_items):
                item = self.plugin_catalog_list.item(idx)
                incompatible = False
                if is_win and key in ("cpu", "mem", "disk", "net", "system", "swap", "processes"):
                    incompatible = True
                elif not is_win and key in ("win_perf", "win_svc"):
                    incompatible = True

                if incompatible:
                    chk.setChecked(False)
                    chk.setEnabled(False)
                    if item:
                        item.setCheckState(Qt.Unchecked)
                        item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                        item.setHidden(True)
                else:
                    chk.setEnabled(True)
                    if item:
                        item.setHidden(False)
                        item.setFlags(item.flags() | Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        finally:
            self._updating_catalog = False

    def _select_all_plugins(self) -> None:
        is_win = bool(
            getattr(self, "ep_os_combo", None)
            and self.ep_os_combo.currentText().strip().lower().startswith("win")
        )
        self._updating_catalog = True
        try:
            for idx, (key, _, _, chk) in enumerate(self.catalog_items):
                item = self.plugin_catalog_list.item(idx)
                if item and item.isHidden():
                    want = False
                elif is_win and key in ("cpu", "mem", "disk", "net", "system", "swap", "processes"):
                    want = False
                elif not is_win and key in ("win_perf", "win_svc"):
                    want = False
                else:
                    want = True
                chk.setChecked(want)
                if item:
                    item.setCheckState(Qt.Checked if want else Qt.Unchecked)
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

    def _create_discovery_executor(self, target: EndpointTarget) -> Any:
        if hasattr(self, "mock_executor") and self.mock_executor is not None:
            return self.mock_executor
        m = target.connection_method.value
        if m == "local":
            return LocalExecutor()
        if m == "winrm" or target.os_family == OSFamily.WINDOWS:
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

    def _on_discover_services_clicked(self) -> None:
        try:
            target = self._get_endpoint_target()
        except Exception as exc:
            QMessageBox.warning(self, "Configuration Incomplete", f"Cannot connect to host: {exc}")
            return

        if target.os_family != OSFamily.WINDOWS:
            QMessageBox.warning(
                self,
                "Windows Only",
                "Windows Services monitoring (inputs.win_services) is only supported on Windows endpoints.",
            )
            return

        if not target.hostname:
            QMessageBox.warning(
                self,
                "Host Required",
                "Please enter an endpoint hostname or IP address in Step 2 before querying services.",
            )
            return

        try:
            executor = self._create_discovery_executor(target)
            services = executor.discover_services()
            if not services:
                QMessageBox.information(
                    self,
                    "No Services Discovered",
                    f"No services could be queried from endpoint {target.hostname}. Verify credentials and remote permissions.",
                )
                return

            current_svcs = [s.strip() for s in self.win_svc_names_input.text().split(",") if s.strip()]
            dlg = ServicesDiscoveryDialog(self, services, initial_selected=current_svcs)
            if dlg.exec() == QDialog.Accepted and dlg.selected_services:
                self.win_svc_names_input.setText(", ".join(dlg.selected_services))
                self.win_svc_check.setChecked(True)
                self.logger.info("Applied %d discovered services to WinServices config", len(dlg.selected_services))
        except Exception as exc:
            self.logger.exception("Failed to discover services on %s", target.hostname)
            QMessageBox.warning(
                self,
                "Service Discovery Error",
                f"Failed to query running services from target host:\n{exc}",
            )

    def _on_browse_perfmon_clicked(self) -> None:
        try:
            target = self._get_endpoint_target()
        except Exception as exc:
            QMessageBox.warning(self, "Configuration Incomplete", f"Cannot connect to host: {exc}")
            return

        if target.os_family != OSFamily.WINDOWS:
            QMessageBox.warning(
                self,
                "Windows Only",
                "Windows Performance Counters are only supported on Windows endpoints.",
            )
            return

        if not target.hostname:
            QMessageBox.warning(
                self,
                "Host Required",
                "Please enter an endpoint hostname or IP address in Step 2 before querying counters.",
            )
            return

        try:
            executor = self._create_discovery_executor(target)
            counter_sets = executor.discover_perfmon_sets()
            if not counter_sets:
                QMessageBox.information(
                    self,
                    "No Counters Discovered",
                    f"No Performance Counter sets could be queried from endpoint {target.hostname}. Verify WinRM connectivity.",
                )
                return

            dlg = PerfmonDiscoveryDialog(self, counter_sets)
            if dlg.exec() == QDialog.Accepted and dlg.selected_sets:
                self.win_perf_check.setChecked(True)
                stanzas = []
                for cs in dlg.selected_sets:
                    clean_name = cs.name.replace(" ", "_").lower()
                    counters = cs.counters if cs.counters else ["*"]
                    counters_toml = ", ".join(f'"{c}"' for c in counters[:8])
                    stanzas.append(
                        f"# Discovered Perfmon Counter Set: {cs.name}\n"
                        f"[[inputs.win_perf_counters]]\n"
                        f"  PrintValid = false\n"
                        f"  [[inputs.win_perf_counters.object]]\n"
                        f'    ObjectName = "{cs.name}"\n'
                        f"    Counters = [{counters_toml}]\n"
                        f'    Instances = ["*"]\n'
                        f'    Measurement = "win_{clean_name}"\n'
                    )
                new_text = "\n".join(stanzas)
                curr = self.custom_toml_input.toPlainText().strip()
                self.custom_toml_input.setPlainText(f"{curr}\n\n{new_text}".strip() if curr else new_text)
                self.custom_toml_check.setChecked(True)
                self.logger.info("Added %d extra Perfmon counter sets to Telegraf configuration", len(dlg.selected_sets))
        except Exception as exc:
            self.logger.exception("Failed to discover perfmon counter sets on %s", target.hostname)
            QMessageBox.warning(
                self,
                "Perfmon Discovery Error",
                f"Failed to query Performance Counter sets from target host:\n{exc}",
            )

    def _on_discover_databases_clicked(self, engine: str) -> None:
        try:
            target = self._get_endpoint_target()
        except Exception as exc:
            QMessageBox.warning(self, "Configuration Incomplete", f"Cannot connect to host: {exc}")
            return

        if not target.hostname:
            QMessageBox.warning(
                self,
                "Host Required",
                "Please enter an endpoint hostname or IP address in Step 2 before discovering databases.",
            )
            return

        default_port = (
            1433
            if engine == "mssql"
            else (5432 if engine in ("postgresql", "postgres") else 3306)
        )
        engine_title = (
            "Microsoft SQL Server"
            if engine == "mssql"
            else ("PostgreSQL" if engine in ("postgresql", "postgres") else "MySQL")
        )
        conn_dlg = DatabaseConnectDialog(
            self,
            engine_name=engine_title,
            default_port=default_port,
        )
        if conn_dlg.exec() != QDialog.Accepted:
            return

        try:
            executor = self._create_discovery_executor(target)
            dbs = executor.discover_databases(
                db_type=engine,
                auth_mode=conn_dlg.auth_mode,
                username=conn_dlg.username or None,
                password=conn_dlg.password or None,
                port=conn_dlg.port,
            )
            if not dbs:
                QMessageBox.information(
                    self,
                    "No Databases Found",
                    f"No online databases found or could not authenticate to {engine_title} on port {conn_dlg.port}.",
                )
                return

            disc_dlg = DatabaseDiscoveryDialog(
                self,
                engine_name=engine_title,
                databases=dbs,
            )
            if disc_dlg.exec() == QDialog.Accepted and disc_dlg.selected_databases:
                selected_db_names = disc_dlg.selected_databases
                if engine == "mssql":
                    self.mssql_check.setChecked(True)
                    if conn_dlg.auth_mode == "sql":
                        conn_str = f"Server=127.0.0.1;Port={conn_dlg.port};User Id={conn_dlg.username};Password={conn_dlg.password};app name=telegraf;log=1;"
                    else:
                        conn_str = f"Server=127.0.0.1;Port={conn_dlg.port};app name=telegraf;log=1;"
                    self.mssql_server_input.setText(conn_str)
                elif engine in ("postgresql", "postgres"):
                    self.postgres_check.setChecked(True)
                    first_db = selected_db_names[0] if selected_db_names else "postgres"
                    pw_part = f" password={conn_dlg.password}" if conn_dlg.password else ""
                    pg_addr = f"host=localhost port={conn_dlg.port} user={conn_dlg.username or 'postgres'}{pw_part} sslmode=disable dbname={first_db}"
                    self.postgres_addr_input.setText(pg_addr)
                elif engine == "mysql":
                    self.mysql_check.setChecked(True)
                    first_db = selected_db_names[0] if selected_db_names else ""
                    db_suffix = f"/{first_db}" if first_db else ""
                    my_srv = f"{conn_dlg.username or 'root'}:{conn_dlg.password or ''}@tcp(127.0.0.1:{conn_dlg.port}){db_suffix}?tls=false"
                    self.mysql_server_input.setText(my_srv)

                self.logger.info("Applied %d discovered %s databases to configuration", len(selected_db_names), engine)
        except Exception as exc:
            self.logger.exception("Failed to discover %s databases on %s", engine, target.hostname)
            QMessageBox.warning(
                self,
                "Database Discovery Error",
                f"Failed to query {engine_title} databases from target host:\n{exc}",
            )
    # Step 4: Review & Preview
    # --------------------------------------------------------------------------
    def _build_review_page(self) -> QWidget:
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

        lbl = QLabel("STEP 5: CONFIGURATION REVIEW & PREVIEW")
        lbl.setProperty("class", "lattice-section-label")
        c_layout.addWidget(lbl)

        desc = QLabel(
            "Review generated Telegraf TOML fragments and planned actions before any execution. "
            "This is an offline template. Dry-run on Step 6 prepares the exact configuration using endpoint discovery and Ops identity."
        )
        desc.setProperty("class", "lattice-muted")
        desc.setWordWrap(True)
        c_layout.addWidget(desc)

        lbl_plan = QLabel("EXECUTION PLAN:")
        lbl_plan.setProperty("class", "lattice-caption")
        c_layout.addWidget(lbl_plan)

        self.review_summary_box = QPlainTextEdit()
        self.review_summary_box.setProperty("class", "code-block")
        self.review_summary_box.setReadOnly(True)
        self.review_summary_box.setMinimumHeight(140)
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

        layout.addWidget(card)
        layout.addStretch()

        scroll.setWidget(content)

        nav_frame, nav_layout = self._build_nav(self.STEP_REVIEW, "Monitoring Inputs", "Execute & Verify")
        refresh_btn = QPushButton("Refresh Preview")
        refresh_btn.clicked.connect(self._update_preview)
        nav_layout.insertWidget(1, refresh_btn)

        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(scroll, 1)
        v.addWidget(nav_frame)
        return page

    def _update_preview(self) -> None:
        target = self._get_endpoint_target()
        env = self._get_vcf_env()
        mon = self._get_monitoring_config()

        is_win = target.os_family == OSFamily.WINDOWS
        conf_dir = getattr(self, "detected_config_dir", None) or ("C:\\telegraf\\telegraf.d" if is_win else "/etc/telegraf/telegraf.d")
        default_ca = f"{conf_dir}\\ca.pem" if is_win else f"{conf_dir}/ca.pem"
        default_cert = f"{conf_dir}\\cert.pem" if is_win else f"{conf_dir}/cert.pem"
        default_key = f"{conf_dir}\\key.pem" if is_win else f"{conf_dir}/key.pem"

        renderer = TelegrafRenderer()
        sys_toml = renderer.render_system_inputs(mon)
        out_toml = renderer.render_vcf_output(
            collector_address=env.collector.address,
            hostname=target.registered_hostname or target.hostname,
            is_windows=is_win,
            vm_mor=target.vm_mor,
            vc_id=target.vc_id,
            verify_ssl=env.agent_verify_ssl,
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

        if target.install_telegraf:
            install_desc = (
                f"Install official InfluxData agent release {target.telegraf_version} package"
                if is_win
                else f"Install official InfluxData agent {target.telegraf_version} via native package manager or archive fallback"
            )
        else:
            install_desc = "Verify existing pre-installed Telegraf agent"


        collector_desc = env.collector.address + (f" ({env.collector.name})" if env.collector.name else "")
        plan_lines = [
            f"Target VM:       {self.bound_vm.name if self.bound_vm else 'none selected'} (MOR {target.vm_mor or 'N/A'})",
            f"Target Endpoint: {target.hostname} ({target.connection_method.value.upper()}, OS: {target.os_family.value}, Port: {target.port})",
            f"VCF Collector:   {collector_desc} (SSL Verify: {env.verify_ssl})",
            "",
            "PLANNED EXECUTION STAGES:",
            f"1. Validate Connectivity: Test connection to {target.hostname} via {target.connection_method.value.upper()} (port {target.port}) and verify VCF Ops Collector reachability.",
            f"2. Acquire Certificates: Connect to VCF Operations Suite API ({env.url}) to acquire mTLS client certificates (ca.cert, client.cert, client.key).",
            f"3. Agent Provisioning: {install_desc}.",
            f"4. Monitoring Configuration: Deploy {conf_dir}/vcf-helper-system.conf ({len(active_plugins)} active plugins: {', '.join(active_plugins)}).",
            f"5. Output Pipeline: Deploy {conf_dir}/cloudproxy-http.conf targeting {env.collector.address} with mTLS authentication.",
            f"6. Mandatory Metadata: Deploy {'mandatory_tags.bat' if is_win else 'mandatory_tags.sh'} to inject VCF Operations resource tags.",
            f"7. Syntax Verification: Run telegraf --test on {target.hostname} to ensure valid configuration syntax before starting service.",
            f"8. Service Activation: Enable and restart Telegraf service ({'Windows Service' if is_win else 'systemd unit'}) and verify telemetry ingestion.",
        ]
        self.review_summary_box.setPlainText("\n".join(plan_lines))
        self.preview_system_box.setPlainText(sys_toml)
        self.preview_output_box.setPlainText(out_toml)
        prepared = getattr(self, "_prepared_config", None)
        if prepared and prepared[0] == self._preview_key():
            self.preview_system_box.setPlainText(prepared[1])
            self.preview_output_box.setPlainText(prepared[2])
            self.review_summary_box.setPlainText("Exact configuration prepared by the last run for these settings.\n" + "\n".join(plan_lines))

    # --------------------------------------------------------------------------
    # Step 5: Execution & Honest Verification
    # --------------------------------------------------------------------------
    def _build_execute_page(self) -> QWidget:
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

        lbl = QLabel("STEP 6: EXECUTION & VERIFICATION")
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
        self.replace_inputs_check = QCheckBox("Replace existing helper inputs")
        self.replace_inputs_check.setToolTip("Unchecked preserves deployed helper inputs; other fragments are always retained.")
        c_layout.addWidget(self.replace_inputs_check)
        self.result_banner = QLabel()
        self.result_banner.setWordWrap(True)
        c_layout.addWidget(self.result_banner)
        self.dry_run_check = QCheckBox("Dry-run (no endpoint changes)")
        action_row.addWidget(self.dry_run_check)

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

        # CLI Command card (Exchange-style repeatability)
        cli_lbl = QLabel("CLI COMMAND TO REPEAT THIS WORKFLOW")
        cli_lbl.setProperty("class", "lattice-section-label")
        c_layout.addWidget(cli_lbl)

        cli_header_row = QHBoxLayout()
        cli_desc = QLabel("Command-line invocation to repeat this exact onboarding workflow from CLI or scripts:")
        cli_desc.setProperty("class", "lattice-muted")
        cli_desc.setWordWrap(True)
        cli_header_row.addWidget(cli_desc)
        cli_header_row.addStretch()

        self.copy_cli_btn = QPushButton("Copy Command")
        self.copy_cli_btn.setProperty("class", "secondary")
        self.copy_cli_btn.clicked.connect(self._copy_cli_command)
        cli_header_row.addWidget(self.copy_cli_btn)
        c_layout.addLayout(cli_header_row)

        self.cli_command_box = QPlainTextEdit()
        self.cli_command_box.setProperty("class", "code-block")
        self.cli_command_box.setReadOnly(True)
        self.cli_command_box.setMinimumHeight(130)
        c_layout.addWidget(self.cli_command_box)

        self.ver_box = self.stage_list_box

        self.dry_run_check.toggled.connect(self._update_cli_command)
        self._update_cli_command()

        layout.addWidget(card)
        layout.addStretch()

        scroll.setWidget(content)

        nav_frame, nav_layout = self._build_nav(self.STEP_EXECUTE, "Review & Preview", None)
        # Same bottom-right slot as every other step's advance button
        self.execute_btn = QPushButton("Execute Guided Workflow ->")
        self.execute_btn.setProperty("class", "primary")
        self.execute_btn.clicked.connect(self._run_workflow)
        nav_layout.addWidget(self.execute_btn)

        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(scroll, 1)
        v.addWidget(nav_frame)
        return page

    def _copy_cli_command(self) -> None:
        cmd = self.cli_command_box.toPlainText()
        if cmd:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(cmd)
            self.copy_cli_btn.setText("Copied!")
            QTimer.singleShot(2000, self, lambda: self.copy_cli_btn.setText("Copy Command"))

    def _build_cli_command(self) -> str:
        target = self._get_endpoint_target()
        env = self._get_vcf_env()
        mon = self._get_monitoring_config()
        dry_run = getattr(self, "dry_run_check", None) and self.dry_run_check.isChecked()
        quote = (lambda value: "'" + value.replace("'", "''") + "'") if sys.platform == "win32" else shlex.quote
        parts = ["vcf-telegraf-helper run"]
        if self.replace_inputs_check.isChecked():
            parts.append("--replace-inputs")
        if not env.agent_verify_ssl:
            parts.append("--no-agent-verify-ssl")
        parts.append(f"--vcf-url {quote(env.url)}")
        use_token_auth = hasattr(self, "vcf_auth_type_combo") and "token" in self.vcf_auth_type_combo.currentText().lower()
        if use_token_auth:
            parts.append('--vcf-token "<token>"')
        elif env.username:
            parts.append(f"--vcf-user {quote(env.username)}")
            if env.auth_source and env.auth_source.lower() != "local":
                parts.append(f"--vcf-auth-source {quote(env.auth_source)}")
            if env.password:
                parts.append('--vcf-pass "<password>"')

        if not env.verify_ssl:
            parts.append("--no-verify-ssl")
        parts.append(f"--collector {quote(env.collector.address)}")
        if env.collector.name:
            parts.append(f"--collector-group {quote(env.collector.name)}")
        if env.ca_cert_path:
            parts.append(f"--ca-cert {quote(env.ca_cert_path)}")

        parts.append(f"--target-host {quote(target.hostname)}")
        parts.append(f"--os {target.os_family.value}")
        parts.append(f"--connection {quote(target.connection_method.value)}")
        std_port = (5986 if target.winrm_use_ssl else 5985) if target.os_family == OSFamily.WINDOWS else 22
        if target.port != std_port:
            parts.append(f"--port {target.port}")

        if target.username:
            parts.append(f"--ssh-user {quote(target.username)}")
        if target.key_filename:
            parts.append(f"--ssh-key {quote(target.key_filename)}")
        else:
            parts.append('--ssh-pass "<password>"')

        if target.winrm_use_ssl:
            parts.append("--winrm-ssl")

        if target.install_telegraf:
            parts.append("--install-telegraf")
            if target.telegraf_version and target.telegraf_version != "1.40.1":
                parts.append(f"--telegraf-version {quote(target.telegraf_version)}")

        reg_host = getattr(target, "registered_hostname", None) or getattr(self, "discovered_hostname", None)
        if reg_host and reg_host != target.hostname:
            parts.append(f"--hostname {quote(reg_host)}")
        if getattr(target, "vm_mor", None):
            parts.append(f"--vm-id {quote(target.vm_mor)}")
            if getattr(target, "vc_id", None):
                parts.append(f"--vc-id {quote(target.vc_id)}")
        if getattr(self, "selected_vm_name", None):
            parts.append(f"--vm-name {quote(self.selected_vm_name)}")

        if target.os_family == OSFamily.WINDOWS:
            has_win_core = mon.win_perf_counters.enabled or (mon.win_services.enabled and bool(mon.win_services.service_names))
            if not has_win_core:
                parts.append("--no-baseline")
            else:
                if not mon.win_perf_counters.enabled:
                    parts.append("--no-win-perf")
                if not mon.win_services.enabled:
                    parts.append("--no-win-services")
                elif mon.win_services.service_names != ["telegraf"]:
                    svcs = ",".join(mon.win_services.service_names)
                    parts.append(f"--win-services {quote(svcs)}")
        else:
            core_plugins = [
                ("cpu", mon.cpu.enabled),
                ("mem", mon.mem.enabled),
                ("disk", mon.disk.enabled),
                ("net", mon.net.enabled),
                ("system", mon.system.enabled),
                ("swap", mon.swap.enabled),
            ]
            enabled_cores = [name for name, en in core_plugins if en]
            if len(enabled_cores) == 0:
                parts.append("--no-baseline")
            elif len(enabled_cores) <= 2:
                parts.append("--no-baseline")
                for name in enabled_cores:
                    parts.append(f"--{name}")
            else:
                for name, en in core_plugins:
                    if not en:
                        parts.append(f"--no-{name}")

            if mon.diskio.enabled:
                parts.append("--diskio")
            if mon.processes.enabled:
                parts.append("--processes")

        if mon.nginx.enabled and mon.nginx.urls:
            parts.append(f"--nginx {quote(mon.nginx.urls[0])}")
        if mon.apache.enabled and mon.apache.urls:
            parts.append(f"--apache {quote(mon.apache.urls[0])}")
        if mon.mysql.enabled and mon.mysql.servers:
            parts.append(f"--mysql {quote(mon.mysql.servers[0])}")
        if mon.postgresql.enabled and mon.postgresql.address:
            parts.append(f"--postgres {quote(mon.postgresql.address)}")
        if mon.mssql.enabled and mon.mssql.servers:
            parts.append(f"--mssql {quote(mon.mssql.servers[0])}")
        if mon.docker.enabled and mon.docker.endpoint:
            parts.append(f"--docker {quote(mon.docker.endpoint)}")
        if mon.ping.enabled and mon.ping.urls:
            parts.append(f"--ping {quote(mon.ping.urls[0])}")

        if dry_run:
            parts.append("--dry-run")

        if sys.platform == "win32":
            return " ".join(parts)
        return " \\\n  ".join(parts)

    def _update_cli_command(self) -> None:
        if hasattr(self, "cli_command_box"):
            self.cli_command_box.setPlainText(self._build_cli_command())

    def _run_workflow(self) -> None:
        self.execute_btn.setEnabled(False)
        self.stage_list_box.clear()
        self.export_md_btn.setEnabled(False)
        self.export_json_btn.setEnabled(False)
        try:
            self._start_workflow_worker()
        except Exception as exc:
            # Setup failed before the worker existed, so no worker signal will re-enable the buttons
            self.logger.exception("Failed to start workflow")
            self._on_worker_failed(f"Could not start the workflow: {exc}")

    def _start_workflow_worker(self) -> None:
        self._update_cli_command()

        target = self._get_endpoint_target()
        env = self._get_vcf_env()
        mon = self._get_monitoring_config()
        opts = WorkflowOptions(
            dry_run=self.dry_run_check.isChecked(),
            replace_inputs=self.replace_inputs_check.isChecked(),
            restart_service=not self.dry_run_check.isChecked(),
            install_telegraf=target.install_telegraf,
            telegraf_version=target.telegraf_version,
        )

        if target.connection_method in (ConnectionMethod.SSH, ConnectionMethod.WINRM) and not target.username:
            raise ValueError("Enter an endpoint username before detecting or applying.")
        self._running_preview_key = self._preview_key()
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
        self.worker.message.connect(self.stage_list_box.appendPlainText)
        self.worker.prepared.connect(self._show_prepared_config)
        self.worker.stage_updated.connect(self._on_worker_stage)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.failed.connect(self._on_worker_failed)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)

        self.worker_thread.start()

    def _preview_key(self):
        return (self._get_endpoint_target().model_dump_json(), self._get_vcf_env().model_dump_json(),
                self._get_monitoring_config().model_dump_json(), self.replace_inputs_check.isChecked())

    def _show_prepared_config(self, system: str, output: str) -> None:
        self._prepared_config = (self._running_preview_key, system, output)
        self.preview_system_box.setPlainText(system)
        self.preview_output_box.setPlainText(output)

    def _on_worker_stage(self, result: StageResult) -> None:
        line = f"[{result.stage.value}] {result.status.value:<7} {result.message}"
        self.stage_list_box.appendPlainText(line)
        if result.details:
            self.stage_list_box.appendPlainText(result.details)

    def _on_worker_finished(self, summary: RunSummary) -> None:
        dry_run = self.dry_run_check.isChecked()
        pending = any(str(v).startswith("PENDING") for v in summary.verifications.values())
        outcome = "FAILED: review the stage details below" if not summary.success else ("DRY-RUN COMPLETE: live verification skipped" if dry_run else ("APPLIED: ingestion confirmation pending" if pending else "SUCCESS: configuration verified"))
        self.result_banner.setText(outcome)
        self.result_banner.setStyleSheet("font-weight: 700; color: " + ("#d95926" if not summary.success or pending else "#199e70") + ";")
        self.last_summary = summary
        self.execute_btn.setEnabled(True)
        self.export_md_btn.setEnabled(True)
        self.export_json_btn.setEnabled(True)

        v_lines = [
            "",
            "============================================================",
            f"WORKFLOW RESULT: {outcome}",
            "============================================================",
        ]
        if summary.verifications:
            for check, status in summary.verifications.items():
                v_lines.append(f"{check:<26}: {status}")
        v_lines.append("============================================================")
        if summary.managed_files:
            v_lines.append("Managed Files:")
            for mf in summary.managed_files:
                v_lines.append(f"  * {mf}")
            v_lines.append("============================================================")
        self.stage_list_box.appendPlainText("\n".join(v_lines))
        self._update_cli_command()

    def _on_worker_failed(self, error: str) -> None:
        self.execute_btn.setEnabled(True)
        self.result_banner.setText("FAILED: " + error)
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
        # The collector is chosen per target in Step 3; before that only the API connection matters
        collector = self._selected_collector() or CollectorInfo(address="")
        use_key = hasattr(self, "vcf_auth_type_combo") and (
            "key" in self.vcf_auth_type_combo.currentText().lower()
            or "token" in self.vcf_auth_type_combo.currentText().lower()
        )
        auth_source = "local"
        if use_key:
            token = self.vcf_token_input.text().strip() or None
            username = "admin"
            password = None
        else:
            token = None
            username = self.vcf_user_input.text().strip()
            password = self.vcf_pass_input.text() or None
            source_text = self.vcf_auth_source_combo.currentText().strip() if hasattr(self, "vcf_auth_source_combo") else ""
            if source_text and source_text.lower() != "local":
                auth_source = source_text

        ca_cert = (self.vcf_ca_input.text().strip() or None) if hasattr(self, "vcf_ca_input") else None
        return VCFEnvironment(
            url=self.vcf_url_input.text().strip() or "https://vcf-ops.local",
            username=username,
            password=password,
            token=token,
            auth_source=auth_source,
            collector=collector,
            verify_ssl=self.vcf_ssl_check.isChecked(),
            agent_verify_ssl=self.agent_ssl_check.isChecked(),
            ca_cert_path=ca_cert,
        )

    def _get_endpoint_target(self) -> EndpointTarget:
        os_str = getattr(self, "ep_os_combo", None)
        is_win = bool(os_str and os_str.currentText().lower().startswith("win"))
        os_family = OSFamily.WINDOWS if is_win else OSFamily.LINUX
        method = ConnectionMethod.WINRM if is_win else ConnectionMethod.SSH
        default_user = None
        try:
            port_val = int(self.ep_port_input.text().strip())
        except Exception:
            port_val = (5986 if self.ep_winrm_ssl_check.isChecked() else 5985) if is_win else 22
        ver_text = self.ep_version_combo.currentText() if hasattr(self, "ep_version_combo") else "1.40.1"
        if "Do Not Install" in ver_text:
            auto_install = False
            ver_str = "1.40.1"
        else:
            auto_install = True
            ver_str = self._get_selected_telegraf_version() if hasattr(self, "_get_selected_telegraf_version") else "1.40.1"

        if is_win:
            key_filename = None
            password = self.ep_pass_input.text()
        else:
            use_key = hasattr(self, "ep_auth_radio_key") and self.ep_auth_radio_key.isChecked()
            if use_key:
                key_filename = self.ep_pass_input.text().strip() or None
                password = None
            else:
                key_filename = None
                password = self.ep_pass_input.text()

        reg_hname = getattr(self, "selected_vm_name", None) or getattr(self, "discovered_hostname", None)

        target = EndpointTarget(
            hostname=self.ep_host_input.text().strip(),
            os_family=os_family,
            connection_method=method,
            port=port_val,
            username=self.ep_user_input.text().strip() or default_user,
            password=password,
            key_filename=key_filename,
            winrm_use_ssl=bool(is_win and self.ep_winrm_ssl_check.isChecked()),
            install_telegraf=auto_install,
            telegraf_version=ver_str,
            registered_hostname=reg_hname,
        )
        if getattr(self, "selected_vm_mor", None):
            target.vm_mor = self.selected_vm_mor
        if getattr(self, "selected_vc_id", None):
            target.vc_id = self.selected_vc_id
        return target

    def _get_monitoring_config(self) -> MonitoringConfig:
        svc_names_raw = self.win_svc_names_input.text().strip() if hasattr(self, "win_svc_names_input") else "*"
        svc_list = [s.strip() for s in svc_names_raw.split(",") if s.strip()] or ["*"]

        nginx_url = self.nginx_url_input.text().strip() if hasattr(self, "nginx_url_input") else "http://localhost/status"
        apache_url = self.apache_url_input.text().strip() if hasattr(self, "apache_url_input") else "http://localhost/server-status?auto"
        mysql_srv = self.mysql_server_input.text().strip() if hasattr(self, "mysql_server_input") else "tcp(127.0.0.1:3306)/"
        pg_addr = self.postgres_addr_input.text().strip() if hasattr(self, "postgres_addr_input") else "host=localhost user=postgres sslmode=disable"
        mssql_srv = self.mssql_server_input.text().strip() if hasattr(self, "mssql_server_input") else "Server=127.0.0.1;Port=1433;User Id=sa;Password=;app name=telegraf;log=1;"
        is_win = (
            self.ep_os_combo.currentText().strip().lower().startswith("win")
            if hasattr(self, "ep_os_combo")
            else False
        )
        default_docker = "npipe:////./pipe/docker_engine" if is_win else "unix:///var/run/docker.sock"
        docker_raw = self.docker_endpoint_input.text().strip() if hasattr(self, "docker_endpoint_input") else default_docker
        if is_win and docker_raw == "unix:///var/run/docker.sock":
            docker_ep = "npipe:////./pipe/docker_engine"
        elif not is_win and docker_raw == "npipe:////./pipe/docker_engine":
            docker_ep = "unix:///var/run/docker.sock"
        else:
            docker_ep = docker_raw or default_docker

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
            system=SystemInputConfig(enabled=bool(not is_win and self.sys_check.isChecked())),
            swap=SwapInputConfig(enabled=bool(not is_win and self.swap_check.isChecked())),
            diskio=DiskIoInputConfig(enabled=bool(getattr(self, "diskio_check", None) and self.diskio_check.isChecked())),
            processes=ProcessesInputConfig(enabled=bool(getattr(self, "proc_check", None) and self.proc_check.isChecked())),
            win_perf_counters=WinPerfCountersInputConfig(
                enabled=bool(is_win and getattr(self, "win_perf_check", None) and self.win_perf_check.isChecked())
            ),
            win_services=WinServicesInputConfig(
                enabled=bool(is_win and getattr(self, "win_svc_check", None) and self.win_svc_check.isChecked()),
                service_names=svc_list,
            ),
            nginx=NginxInputConfig(enabled=bool(getattr(self, "nginx_check", None) and self.nginx_check.isChecked()), urls=[nginx_url]),
            apache=ApacheInputConfig(enabled=bool(getattr(self, "apache_check", None) and self.apache_check.isChecked()), urls=[apache_url]),
            mysql=MysqlInputConfig(enabled=bool(getattr(self, "mysql_check", None) and self.mysql_check.isChecked()), servers=[mysql_srv]),
            postgresql=PostgresqlInputConfig(enabled=bool(getattr(self, "postgres_check", None) and self.postgres_check.isChecked()), address=pg_addr),
            mssql=MssqlInputConfig(enabled=bool(getattr(self, "mssql_check", None) and self.mssql_check.isChecked()), servers=[mssql_srv]),
            docker=DockerInputConfig(enabled=bool(getattr(self, "docker_check", None) and self.docker_check.isChecked()), endpoint=docker_ep),
            ping=PingInputConfig(enabled=bool(getattr(self, "ping_check", None) and self.ping_check.isChecked()), urls=[ping_url]),
            custom_toml=custom_txt,
        )

    def _create_executor(self, target: EndpointTarget) -> Any:
        m = target.connection_method.value
        if m == "local":
            return LocalExecutor()
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
            self.vcf_user_input.setText(latest.username or "")
            if latest.auth_source and latest.auth_source.lower() != "local":
                if hasattr(self, "vcf_auth_source_combo"):
                    if self.vcf_auth_source_combo.findText(latest.auth_source) < 0:
                        self.vcf_auth_source_combo.addItem(latest.auth_source)
                    self.vcf_auth_source_combo.setCurrentText(latest.auth_source)
            self.vcf_ssl_check.setChecked(latest.verify_ssl)
            if latest.url:
                self._load_auth_sources()

        saved_vcf_mode = self.state_store.get_preference("vcf_auth_mode")
        if saved_vcf_mode and hasattr(self, "vcf_auth_type_combo"):
            idx = self.vcf_auth_type_combo.findText(saved_vcf_mode)
            if idx >= 0:
                self.vcf_auth_type_combo.setCurrentIndex(idx)
        self._update_vcf_auth_visibility()

        self._on_vcf_ssl_toggled(self.vcf_ssl_check.isChecked())
