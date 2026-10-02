"""Modal dialogs for live endpoint guest discovery (services, perfmon, databases)."""

from __future__ import annotations

from typing import List, Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vcf_ops_telegraf_helper.models.discovery import (
    DiscoveredDatabase,
    DiscoveredPerfmonSet,
    DiscoveredService,
)


class ServicesDiscoveryDialog(QDialog):
    """Modal dialog displaying discovered running host services with selection."""

    def __init__(
        self,
        parent: Optional[QWidget],
        services: List[DiscoveredService],
        initial_selected: Optional[List[str]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Discovered Host Services")
        self.resize(720, 520)
        self.services = services
        self.selected_services: List[str] = []
        self._initial_selected = set(initial_selected or [])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header card
        header = QFrame()
        header.setProperty("class", "lattice-card")
        h_layout = QVBoxLayout(header)
        h_layout.setContentsMargins(12, 10, 12, 10)
        h_layout.setSpacing(4)

        title = QLabel("LIVE GUEST SERVICE DISCOVERY")
        title.setProperty("class", "lattice-section-label")
        h_layout.addWidget(title)

        desc = QLabel(
            "Discovered active services from the target endpoint. "
            "Select services to monitor via Telegraf inputs.win_services or systemd units."
        )
        desc.setProperty("class", "lattice-muted")
        desc.setWordWrap(True)
        h_layout.addWidget(desc)
        layout.addWidget(header)

        # Search filter and bulk buttons
        ctrl_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter services by name or description...")
        self.search_input.textChanged.connect(self._filter_table)
        ctrl_row.addWidget(self.search_input, 1)

        btn_running = QPushButton("Select Running")
        btn_running.setProperty("class", "secondary")
        btn_running.clicked.connect(self._select_running)
        ctrl_row.addWidget(btn_running)

        btn_all = QPushButton("Select All")
        btn_all.setProperty("class", "secondary")
        btn_all.clicked.connect(self._select_all)
        ctrl_row.addWidget(btn_all)

        btn_clear = QPushButton("Clear")
        btn_clear.setProperty("class", "secondary")
        btn_clear.clicked.connect(self._clear_all)
        ctrl_row.addWidget(btn_clear)

        layout.addLayout(ctrl_row)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Select", "Service Name", "Display Name", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table, 1)

        self._populate_table(self.services)

        # Bottom summary and actions
        bottom_row = QHBoxLayout()
        self.count_label = QLabel(f"Total services: {len(self.services)}")
        self.count_label.setProperty("class", "lattice-caption")
        bottom_row.addWidget(self.count_label)
        bottom_row.addStretch()

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Apply Selected Services")
        btns.button(QDialogButtonBox.Ok).setProperty("class", "primary")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        bottom_row.addWidget(btns)

        layout.addLayout(bottom_row)

    def _populate_table(self, services: List[DiscoveredService]) -> None:
        self.table.setRowCount(len(services))
        for row, svc in enumerate(services):
            # Checkbox item
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            is_checked = svc.name in self._initial_selected or (not self._initial_selected and svc.status.lower() == "running" and svc.name.lower() in ("telegraf", "mssqlserver", "docker", "nginx", "apache2", "w3svc"))
            chk_item.setCheckState(Qt.Checked if is_checked else Qt.Unchecked)
            chk_item.setData(Qt.UserRole, svc.name)
            self.table.setItem(row, 0, chk_item)

            name_item = QTableWidgetItem(svc.name)
            name_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, 1, name_item)

            disp_item = QTableWidgetItem(svc.display_name or svc.name)
            disp_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, 2, disp_item)

            st_item = QTableWidgetItem(svc.status.capitalize())
            st_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if svc.status.lower() == "running":
                st_item.setForeground(Qt.darkGreen)
            self.table.setItem(row, 3, st_item)

    def _filter_table(self, query: str) -> None:
        q = query.strip().lower()
        for row in range(self.table.rowCount()):
            name = (self.table.item(row, 1).text() if self.table.item(row, 1) else "").lower()
            disp = (self.table.item(row, 2).text() if self.table.item(row, 2) else "").lower()
            show = not q or q in name or q in disp
            self.table.setRowHidden(row, not show)

    def _select_running(self) -> None:
        for row in range(self.table.rowCount()):
            st = (self.table.item(row, 3).text() if self.table.item(row, 3) else "").lower()
            chk = self.table.item(row, 0)
            if chk:
                chk.setCheckState(Qt.Checked if st == "running" else Qt.Unchecked)

    def _select_all(self) -> None:
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                chk = self.table.item(row, 0)
                if chk:
                    chk.setCheckState(Qt.Checked)

    def _clear_all(self) -> None:
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 0)
            if chk:
                chk.setCheckState(Qt.Unchecked)

    def _on_accept(self) -> None:
        self.selected_services = []
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 0)
            if chk and chk.checkState() == Qt.Checked:
                svc_name = chk.data(Qt.UserRole) or self.table.item(row, 1).text()
                if svc_name:
                    self.selected_services.append(svc_name)
        self.accept()


class PerfmonDiscoveryDialog(QDialog):
    """Modal dialog displaying discovered Windows Performance Counter sets."""

    def __init__(
        self,
        parent: Optional[QWidget],
        counter_sets: List[DiscoveredPerfmonSet],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Windows Performance Counter Browser")
        self.resize(780, 560)
        self.counter_sets = counter_sets
        self.selected_sets: List[DiscoveredPerfmonSet] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QFrame()
        header.setProperty("class", "lattice-card")
        h_layout = QVBoxLayout(header)
        h_layout.setContentsMargins(12, 10, 12, 10)
        h_layout.setSpacing(4)

        title = QLabel("PERFMON COUNTER SET DISCOVERY")
        title.setProperty("class", "lattice-section-label")
        h_layout.addWidget(title)

        desc = QLabel(
            "Discovered installed Performance Counter sets from the Windows guest. "
            "Select counter sets to add to Telegraf inputs.win_perf_counters."
        )
        desc.setProperty("class", "lattice-muted")
        desc.setWordWrap(True)
        h_layout.addWidget(desc)
        layout.addWidget(header)

        # Search bar
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter counter sets by name or description...")
        self.search_input.textChanged.connect(self._filter_table)
        layout.addWidget(self.search_input)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Select", "Counter Set Name", "Description", "Counters Found"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table, 1)

        self._populate_table(self.counter_sets)

        bottom_row = QHBoxLayout()
        self.count_label = QLabel(f"Total sets: {len(self.counter_sets)}")
        self.count_label.setProperty("class", "lattice-caption")
        bottom_row.addWidget(self.count_label)
        bottom_row.addStretch()

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Add to Telegraf Config")
        btns.button(QDialogButtonBox.Ok).setProperty("class", "primary")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        bottom_row.addWidget(btns)

        layout.addLayout(bottom_row)

    def _populate_table(self, sets: List[DiscoveredPerfmonSet]) -> None:
        self.table.setRowCount(len(sets))
        for row, cs in enumerate(sets):
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk_item.setCheckState(Qt.Unchecked)
            chk_item.setData(Qt.UserRole, cs)
            self.table.setItem(row, 0, chk_item)

            name_item = QTableWidgetItem(cs.name)
            name_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, 1, name_item)

            desc_item = QTableWidgetItem(cs.description or "Performance counter set")
            desc_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, 2, desc_item)

            cnt_item = QTableWidgetItem(f"{len(cs.counters)} counters")
            cnt_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, 3, cnt_item)

    def _filter_table(self, query: str) -> None:
        q = query.strip().lower()
        for row in range(self.table.rowCount()):
            name = (self.table.item(row, 1).text() if self.table.item(row, 1) else "").lower()
            desc = (self.table.item(row, 2).text() if self.table.item(row, 2) else "").lower()
            show = not q or q in name or q in desc
            self.table.setRowHidden(row, not show)

    def _on_accept(self) -> None:
        self.selected_sets = []
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 0)
            if chk and chk.checkState() == Qt.Checked:
                cs = chk.data(Qt.UserRole)
                if cs:
                    self.selected_sets.append(cs)
        self.accept()


class DatabaseDiscoveryDialog(QDialog):
    """Modal dialog displaying discovered database catalogs for MSSQL, Postgres, MySQL."""

    def __init__(
        self,
        parent: Optional[QWidget],
        engine_name: str,
        databases: List[DiscoveredDatabase],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Discovered {engine_name} Catalogs")
        self.resize(680, 480)
        self.engine_name = engine_name
        self.databases = databases
        self.selected_databases: List[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QFrame()
        header.setProperty("class", "lattice-card")
        h_layout = QVBoxLayout(header)
        h_layout.setContentsMargins(12, 10, 12, 10)
        h_layout.setSpacing(4)

        title = QLabel(f"DISCOVERED {engine_name.upper()} DATABASES")
        title.setProperty("class", "lattice-section-label")
        h_layout.addWidget(title)

        desc = QLabel(
            f"Discovered active database catalogs on the target {engine_name} instance. "
            "Select databases to monitor."
        )
        desc.setProperty("class", "lattice-muted")
        desc.setWordWrap(True)
        h_layout.addWidget(desc)
        layout.addWidget(header)

        # Search bar
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter databases by name...")
        self.search_input.textChanged.connect(self._filter_table)
        layout.addWidget(self.search_input)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Select", "Database Name", "State", "Size (MB)"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table, 1)

        self._populate_table(self.databases)

        bottom_row = QHBoxLayout()
        self.count_label = QLabel(f"Total databases: {len(self.databases)}")
        self.count_label.setProperty("class", "lattice-caption")
        bottom_row.addWidget(self.count_label)
        bottom_row.addStretch()

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Apply Database Selection")
        btns.button(QDialogButtonBox.Ok).setProperty("class", "primary")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        bottom_row.addWidget(btns)

        layout.addLayout(bottom_row)

    def _populate_table(self, databases: List[DiscoveredDatabase]) -> None:
        self.table.setRowCount(len(databases))
        for row, db in enumerate(databases):
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            # Default check user databases, uncheck system dbs (master, tempdb, model, msdb)
            is_sys = db.name.lower() in ("master", "tempdb", "model", "msdb", "postgres", "information_schema")
            chk_item.setCheckState(Qt.Unchecked if is_sys else Qt.Checked)
            chk_item.setData(Qt.UserRole, db.name)
            self.table.setItem(row, 0, chk_item)

            name_item = QTableWidgetItem(db.name)
            name_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, 1, name_item)

            st_item = QTableWidgetItem(db.state.capitalize())
            st_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if db.state.lower() == "online":
                st_item.setForeground(Qt.darkGreen)
            self.table.setItem(row, 2, st_item)

            size_str = f"{db.size_mb:.1f} MB" if db.size_mb is not None else "N/A"
            size_item = QTableWidgetItem(size_str)
            size_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, 3, size_item)

    def _filter_table(self, query: str) -> None:
        q = query.strip().lower()
        for row in range(self.table.rowCount()):
            name = (self.table.item(row, 1).text() if self.table.item(row, 1) else "").lower()
            show = not q or q in name
            self.table.setRowHidden(row, not show)

    def _on_accept(self) -> None:
        self.selected_databases = []
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 0)
            if chk and chk.checkState() == Qt.Checked:
                db_name = chk.data(Qt.UserRole) or self.table.item(row, 1).text()
                if db_name:
                    self.selected_databases.append(db_name)
        self.accept()


class DatabaseConnectDialog(QDialog):
    """Modal dialog to collect database connection parameters for discovery."""

    def __init__(
        self,
        parent: Optional[QWidget],
        engine_name: str,
        default_port: int,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Connect to {engine_name}")
        self.resize(480, 320)
        # Normalize display titles to the stable engine keys the branches below test against
        self.engine_name = {
            "microsoft sql server": "mssql",
            "sql server": "mssql",
            "postgres": "postgresql",
        }.get(engine_name.strip().lower(), engine_name.strip().lower())
        self.auth_mode = "integrated"
        self.username = ""
        self.password = ""
        self.port = default_port

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QFrame()
        header.setProperty("class", "lattice-card")
        h_layout = QVBoxLayout(header)
        h_layout.setContentsMargins(12, 10, 12, 10)
        h_layout.setSpacing(4)

        title = QLabel(f"QUERY {engine_name.upper()} INSTANCE")
        title.setProperty("class", "lattice-section-label")
        h_layout.addWidget(title)

        desc = QLabel(
            f"Specify connection parameters to query active database catalogs on {engine_name}."
        )
        desc.setProperty("class", "lattice-muted")
        desc.setWordWrap(True)
        h_layout.addWidget(desc)
        layout.addWidget(header)

        # Fields
        form = QGridLayout()
        form.setSpacing(10)

        form.addWidget(QLabel("Port:"), 0, 0)
        self.port_input = QLineEdit(str(default_port))
        form.addWidget(self.port_input, 0, 1)

        row = 1
        if self.engine_name == "mssql":
            form.addWidget(QLabel("Authentication:"), row, 0)
            self.radio_integrated = QRadioButton("Windows Integrated (WinRM User)")
            self.radio_sql = QRadioButton("SQL Server Authentication")
            self.radio_integrated.setChecked(True)
            self.auth_grp = QButtonGroup(self)
            self.auth_grp.addButton(self.radio_integrated)
            self.auth_grp.addButton(self.radio_sql)
            self.radio_integrated.toggled.connect(self._on_auth_toggled)
            self.radio_sql.toggled.connect(self._on_auth_toggled)
            radio_box = QVBoxLayout()
            radio_box.addWidget(self.radio_integrated)
            radio_box.addWidget(self.radio_sql)
            form.addLayout(radio_box, row, 1)
            row += 1
        elif self.engine_name in ("postgresql", "postgres"):
            form.addWidget(QLabel("Authentication:"), row, 0)
            self.radio_peer = QRadioButton("Local Peer (sudo -u postgres)")
            self.radio_pw = QRadioButton("Database Password")
            self.radio_peer.setChecked(True)
            self.auth_grp = QButtonGroup(self)
            self.auth_grp.addButton(self.radio_peer)
            self.auth_grp.addButton(self.radio_pw)
            self.radio_peer.toggled.connect(self._on_auth_toggled)
            self.radio_pw.toggled.connect(self._on_auth_toggled)
            radio_box = QVBoxLayout()
            radio_box.addWidget(self.radio_peer)
            radio_box.addWidget(self.radio_pw)
            form.addLayout(radio_box, row, 1)
            row += 1

        self.user_lbl = QLabel("Username:")
        self.user_input = QLineEdit()
        if self.engine_name == "mssql":
            self.user_input.setText("sa")
            self.user_lbl.setVisible(False)
            self.user_input.setVisible(False)
        elif self.engine_name in ("postgresql", "postgres"):
            self.user_input.setText("postgres")
            self.user_lbl.setVisible(False)
            self.user_input.setVisible(False)
        elif self.engine_name == "mysql":
            self.user_input.setText("root")
        form.addWidget(self.user_lbl, row, 0)
        form.addWidget(self.user_input, row, 1)
        row += 1

        self.pass_lbl = QLabel("Password:")
        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.Password)
        if self.engine_name in ("mssql", "postgresql", "postgres"):
            self.pass_lbl.setVisible(False)
            self.pass_input.setVisible(False)
        form.addWidget(self.pass_lbl, row, 0)
        form.addWidget(self.pass_input, row, 1)

        layout.addLayout(form)
        layout.addStretch()

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Connect & Discover")
        btns.button(QDialogButtonBox.Ok).setProperty("class", "primary")
        btns.accepted.connect(self._on_submit)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_auth_toggled(self) -> None:
        if self.engine_name == "mssql":
            is_sql = self.radio_sql.isChecked()
            self.user_lbl.setVisible(is_sql)
            self.user_input.setVisible(is_sql)
            self.pass_lbl.setVisible(is_sql)
            self.pass_input.setVisible(is_sql)
        elif self.engine_name in ("postgresql", "postgres"):
            is_pw = self.radio_pw.isChecked()
            self.user_lbl.setVisible(is_pw)
            self.user_input.setVisible(is_pw)
            self.pass_lbl.setVisible(is_pw)
            self.pass_input.setVisible(is_pw)

    def _on_submit(self) -> None:
        try:
            self.port = int(self.port_input.text().strip())
        except ValueError:
            self.port = (
                1433
                if self.engine_name == "mssql"
                else (5432 if self.engine_name in ("postgresql", "postgres") else 3306)
            )

        if self.engine_name == "mssql":
            self.auth_mode = "sql" if self.radio_sql.isChecked() else "integrated"
        elif self.engine_name in ("postgresql", "postgres"):
            self.auth_mode = "password" if self.radio_pw.isChecked() else "integrated"
        else:
            self.auth_mode = "password"

        self.username = self.user_input.text().strip()
        self.password = self.pass_input.text()
        self.accept()
