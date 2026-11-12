"""The window of Telegraf Helper, for VCF Operations.

The app is shaped like the work: a list of machines, each with a plain status, and three
places to go (Machines, Activity, Settings). Anything done to machines is a short guided path
with its steps listed on the left. This module is the frame: the left column, the place the
current screen sits, the connection to VCF Operations, and the list of machines. The screens
themselves live beside it (screens_machines, flow, screens_more, dialogs).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from vcf_ops_telegraf_helper import __version__
from vcf_ops_telegraf_helper.adapters.base import VCFOpsIntegration
from vcf_ops_telegraf_helper.adapters.factory import get_adapter
from vcf_ops_telegraf_helper.executors.local import LocalExecutor
from vcf_ops_telegraf_helper.executors.ssh import SSHExecutor
from vcf_ops_telegraf_helper.executors.winrm import WinRMExecutor
from vcf_ops_telegraf_helper.gui.model import Machine, Session, Settings
from vcf_ops_telegraf_helper.gui.theme import build_stylesheet, tokens
from vcf_ops_telegraf_helper.gui.widgets import (
    adopt,
    Banner,
    Dialog,
    Field,
    Page,
    box,
    busy_bar,
    button,
    esc,
    hbox,
    label,
    link,
    stretch,
    vbox,
)
from vcf_ops_telegraf_helper.gui.workers import Tasks
from vcf_ops_telegraf_helper.logger import get_logger
from vcf_ops_telegraf_helper.models.endpoint import EndpointTarget
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo, VCFEnvironment
from vcf_ops_telegraf_helper.security.certs import PresentedCertificate, fetch_presented_certificate, host_and_port
from vcf_ops_telegraf_helper.storage.history import HistoryStore, format_clock
from vcf_ops_telegraf_helper.storage.journal import TakeoverJournal
from vcf_ops_telegraf_helper.storage.state import StateStore
from vcf_ops_telegraf_helper.workflow.connect import ConnectOutcome, address_to_url, connect, normalize_address
from vcf_ops_telegraf_helper.workflow.plain import (
    APP_NAME,
    APP_TAGLINE,
    STATUS_ATTENTION,
    STATUS_INTERRUPTED,
    STATUS_SENDING,
    STATUS_WAITING,
    machine_status,
    plural,
)
from vcf_ops_telegraf_helper.workflow.plan import ConnectionChoices

LOCAL_ACCOUNT = "An account made in VCF Operations itself"
COMPANY_ACCOUNT = "My company login"

SIDE_WIDTH = 196


class MainWindow(QMainWindow):
    """Main window: the left column, the current screen, and what the app knows."""

    def __init__(
        self,
        state_store: Optional[StateStore] = None,
        history: Optional[HistoryStore] = None,
        journal: Optional[TakeoverJournal] = None,
    ) -> None:
        super().__init__()
        self.logger = get_logger("gui")
        self.state_store = state_store or StateStore()
        # History and takeover records live next to the state file unless a caller places them
        folder = self.state_store.state_file.parent
        self.history = history or HistoryStore(folder / "history.json")
        self.journal = journal or TakeoverJournal(folder / "takeovers")
        self.settings = Settings.load(self.state_store)
        self.current_theme = self.settings.theme if self.settings.theme in ("dark", "light") else "dark"
        self.tasks = Tasks(self)
        self.session = Session()

        # Stand-ins replace these in tests and in a throwaway setup
        self.fetch_certificate: Callable[[str], PresentedCertificate] = fetch_presented_certificate
        self.package_fetcher: Optional[Callable[[str, bool, bool], tuple]] = None
        self.takeover_waits: Dict[str, int] = {}

        self.conn: Optional[ConnectionChoices] = None
        self.adapter: Optional[VCFOpsIntegration] = None
        self.connected = False
        self.machines: List[Machine] = []
        self.collectors: List[CollectorInfo] = []
        self.inventory_note: Optional[str] = None
        self.platform_note: Optional[str] = None
        self.loading = False
        self.load_error: Optional[str] = None
        self.arrived: set = set()  # machines whose first data this app has seen arrive
        self.selection: List[str] = []
        self.filter = "all"
        self.query = ""
        self.show_off = False
        self.show_infra = False
        self.banner: Optional[tuple] = None  # (kind, html) shown once on Machines
        self.flow: Any = None
        self.working_on: Optional[str] = None  # a machine being changed outside a guided path (removal)
        self.screen = "connect"
        self.page: Optional[Page] = None
        self.machine_id: Optional[str] = None

        # The Connect form outlives the screen so a failed attempt keeps what was typed
        self.form: Dict[str, Any] = {
            "address": "", "user": "", "password": "", "token": "", "use_token": False, "source": LOCAL_ACCOUNT,
            "sources": [], "sources_for": None, "returning": False, "busy": False, "error": None, "note": None,
            "tries": 0, "tried_at": "", "cert": None, "cert_changed": False, "cert_note": None,
        }
        self._release_notice = None
        self._update_check_started = False

        self.setWindowTitle(f"{APP_NAME}, {APP_TAGLINE}")
        self.setWindowIcon(QIcon(str(Path(__file__).parent / "assets/app.svg")))
        self.resize(1000, 700)
        self.setMinimumSize(980, 640)

        self._init_ui()
        self._apply_theme()
        self._load_saved_state()
        self.show_connect()
        app = QApplication.instance()
        if app is not None:
            # However the program ends, every widget belongs to Qt by then
            app.aboutToQuit.connect(lambda: adopt(self))
        self.logger.info("MainWindow initialized")

    # ------------------------------------------------------------------ frame
    def _init_ui(self) -> None:
        central = QWidget(self)
        central.setProperty("class", "page")
        self.setCentralWidget(central)
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.side = QFrame()
        self.side.setObjectName("side")
        self.side.setProperty("class", "side")
        self.side.setFixedWidth(SIDE_WIDTH)
        self.side_layout = QVBoxLayout(self.side)
        self.side_layout.setContentsMargins(10, 14, 10, 10)
        self.side_layout.setSpacing(2)
        row.addWidget(self.side)
        self.holder = QWidget()
        self.holder.setProperty("class", "page")
        self.holder_layout = QVBoxLayout(self.holder)
        self.holder_layout.setContentsMargins(0, 0, 0, 0)
        self.holder_layout.setSpacing(0)
        row.addWidget(self.holder, 1)
        self.toast_label = label("", "toast", wrap=False, name="toast")
        self.toast_label.setParent(central)
        self.toast_label.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast_label.hide)

    def set_page(self, page: Page, screen: Optional[str] = None) -> Page:
        """Put a screen in the window, replacing the one that was there."""
        old = self.page
        if old is not None:
            if old.on_leave:
                old.on_leave()
            self.holder_layout.removeWidget(old)
            old.hide()
            old.deleteLater()
        self.page = page
        if screen:
            self.screen = screen
        self.holder_layout.addWidget(page)
        self.render_side()
        adopt(page)
        page.show()
        if page.on_enter:
            page.on_enter()
        return page

    def toast(self, text: str) -> None:
        """A short confirmation that fades by itself."""
        self.toast_label.setText(esc(text))
        self.toast_label.adjustSize()
        central = self.centralWidget()
        self.toast_label.move((central.width() - self.toast_label.width()) // 2, central.height() - 64 - self.toast_label.height())
        self.toast_label.show()
        self.toast_label.raise_()
        self._toast_timer.start(2600)

    def brand(self) -> QWidget:
        icon = label("", "clear", wrap=False)
        icon.setPixmap(QIcon(str(Path(__file__).parent / "assets/app.svg")).pixmap(QSize(30, 30)))
        icon.setFixedSize(30, 30)
        text = vbox(label(APP_NAME, "brand", wrap=False), label(APP_TAGLINE, "brand-sub", wrap=False), spacing=0)
        return box(hbox(icon, (box(text), 1), spacing=9, margins=(6, 0, 6, 14)))

    def render_side(self) -> None:
        """The left column: main navigation, or the steps of the guided path in progress."""
        from vcf_ops_telegraf_helper.gui.widgets import _empty

        _empty(self.side_layout)
        self.side.setVisible(self.connected)
        if not self.connected:
            return
        self.side_layout.addWidget(self.brand())
        if self.flow is not None:
            self.flow.fill_side(self.side_layout)
            adopt(self.side)
            return
        fix = 0 if (self.loading or self.load_error) else sum(
            1 for m in self.listed() if m.status in (STATUS_ATTENTION, STATUS_INTERRUPTED))
        current = {"machine": "machines", "entry": "activity"}.get(self.screen, self.screen)
        if self.screen == "entry" and getattr(self, "entry_from_machine", None):
            current = "machines"
        for key, text, go in (("machines", "Machines", lambda: self.show_machines()),
                              ("activity", "Activity", self.show_activity),
                              ("settings", "Settings", lambda: self.show_settings())):
            nav = button(text, go, "nav", name=f"nav-{key}")
            nav.setCheckable(True)
            nav.setChecked(current == key)
            nav.setEnabled(not self.working_on)
            if key == "machines" and fix:
                badge = label(f"{fix} to fix", "nav-badge", wrap=False, name="nav-badge")
                inner = hbox(stretch, badge, margins=(0, 0, 8, 0))
                nav.setLayout(inner)
            self.side_layout.addWidget(nav)
        self.side_layout.addStretch(1)
        if self._release_notice is not None and self.settings.updates:
            notice = label(
                f'<a href="{self._release_notice.url}">New version available: {esc(self._release_notice.version)}</a>',
                "small", name="update-notice")
            notice.setTextInteractionFlags(Qt.TextBrowserInteraction)
            notice.setOpenExternalLinks(True)
            self.side_layout.addWidget(notice)
            self.side_layout.addSpacing(6)
        host = normalize_address(self.conn.url) if self.conn else ""
        who = "an access token" if (self.conn and self.conn.use_token) else (self.conn.username if self.conn else "")
        colours = tokens(self.current_theme)
        conn = button("", lambda: self.show_settings("conn"), "conn", name="side-connection")
        words = label(
            f'<span style="color:{colours["ok"]}">●</span> <b style="color:{colours["ink"]}">Connected</b><br>'
            f'{esc(host)}<br>as {esc(who)}<br><span style="color:{colours["link"]}"><u>Test or change</u></span>', "small")
        words.setAttribute(Qt.WA_TransparentForMouseEvents)
        conn.setLayout(vbox(words, margins=(9, 7, 9, 7)))
        conn.setMinimumHeight(words.sizeHint().height() + 16)
        conn.setAccessibleName(f"Connected to {host} as {who}. Test or change")
        conn.setToolTip("Test the connection to VCF Operations, or connect as someone else")
        conn.setEnabled(not self.working_on)
        self.side_layout.addWidget(conn)
        self.side_layout.addSpacing(6)
        self.side_layout.addWidget(link("About this app", self._show_about_dialog, name="side-about"))
        adopt(self.side)

    # ------------------------------------------------------------------ theme, about, updates
    def _apply_theme(self) -> None:
        self.setStyleSheet(build_stylesheet(self.current_theme))

    def set_theme(self, theme: str) -> None:
        """Change the colours straight away and remember the choice."""
        self.current_theme = theme
        self.settings.theme = theme
        self.settings.save(self.state_store)
        self._apply_theme()

    def _toggle_theme(self) -> None:
        self.set_theme("light" if self.current_theme == "dark" else "dark")

    def _show_release_notice(self, notice) -> None:
        self._release_notice = notice
        if self.flow is None:
            self.render_side()

    def start_update_check(self) -> None:
        """Check after launch, without holding up startup or application shutdown."""
        if self._update_check_started or not self.settings.updates:
            return
        self._update_check_started = True
        from vcf_ops_telegraf_helper.updates import check_release

        cache_path = self.state_store.state_file.parent / "release-check.json"
        self.tasks.run(lambda: check_release(__version__, cache_path),
                       done=lambda notice: self._show_release_notice(notice) if notice else None,
                       failed=lambda _exc: None, name="release-check")

    def _show_about_dialog(self) -> None:
        from vcf_ops_telegraf_helper.gui.dialogs import show_text
        from vcf_ops_telegraf_helper.notices import get_third_party_notices

        dialog = Dialog(self, APP_NAME, f"Version {esc(__version__)}", name="about-dialog")
        dialog.add(label(
            "Sets up open-source Telegraf on your machines so they report to VCF Operations 9.1.<br><br>"
            "Its full name is VCF Operations Open Telegraf Helper. Made by Scott. MIT licence. "
            "It includes other open-source software.", "plain"))
        dialog.add_button("Open-source licences",
                          lambda: show_text(self, "Open-source licences", get_third_party_notices()), name="about-licences")
        dialog.add_button("Close", dialog.accept, "primary", name="about-close")
        dialog.exec()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if (self.flow is not None and self.flow.changing_now()) or self.working_on:
            QMessageBox.warning(
                self, "Work in progress",
                "The app is working on a machine right now, and closing it would leave that half done with no record of "
                "it. Wait for it to finish, then close the app. (On the running screen, Stop leaves alone the machines "
                "not yet changed.) If the app is closed anyway during a takeover, open it again and it will offer to "
                "finish from where it stopped.",
            )
            event.ignore()
            return
        if self.flow is not None:
            self.flow.abandon()
        self.tasks.close()
        adopt(self)
        super().closeEvent(event)

    # ------------------------------------------------------------------ stand-in seams
    def make_adapter(self, env: VCFEnvironment) -> VCFOpsIntegration:
        return get_adapter(env)

    def _create_executor(self, target: EndpointTarget) -> Any:
        method = target.connection_method.value
        if method == "local":
            return LocalExecutor()
        if method == "winrm":
            return WinRMExecutor(hostname=target.hostname, port=target.port, username=target.username,
                                 password=target.password, use_ssl=target.winrm_use_ssl)
        return SSHExecutor(hostname=target.hostname, port=target.port, username=target.username,
                           password=target.password, key_filename=target.key_filename)

    def make_executor(self, target: EndpointTarget) -> Any:
        return self._create_executor(target)

    # ------------------------------------------------------------------ saved state
    def _load_saved_state(self) -> None:
        environments = self.state_store.list_environments()
        if not environments:
            return
        latest = environments[-1]
        self.form["address"] = normalize_address(latest.url)
        self.form["user"] = latest.username or ""
        self.form["use_token"] = self.state_store.get_preference("vcf_auth_mode") == "token"
        if latest.auth_source and latest.auth_source.lower() != "local":
            self.form["source"] = f"{COMPANY_ACCOUNT}: {latest.auth_source}"
            self.form["sources"] = [latest.auth_source]
            self.form["sources_for"] = self.form["address"]
        self.form["returning"] = bool(self.form["address"] and (self.form["user"] or self.form["use_token"]))

    # ------------------------------------------------------------------ connect
    def show_connect(self) -> None:
        if self.form.get("cert") is not None and self.form.get("error") == "cert":
            self.set_page(self._certificate_page(), "connect")
        else:
            self.set_page(self._connect_page(), "connect")

    def _connect_page(self) -> Page:
        form = self.form
        page = Page("connect", margins=(22, 18, 22, 18), spacing=0)
        column = QVBoxLayout()
        column.setSpacing(0)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.brand())
        if form["returning"]:
            column.addWidget(label("Welcome back", "h1-big", name="page-title"))
            column.addWidget(label("Enter your password to connect to VCF Operations again. The password is never saved.",
                                   "sub", name="page-sub"))
        else:
            column.addWidget(label("Connect to VCF Operations", "h1-big", name="page-title"))
            column.addWidget(label(
                "This app sets up monitoring on your machines so they report in detail to VCF Operations. "
                "First, tell it where VCF Operations is and sign in.", "sub", name="page-sub"))
        column.addSpacing(12)
        address = Field("VCF Operations address", "The same address you type in a browser to open VCF Operations.",
                        placeholder="for example: ops.example.com", name="connect-address", value=form["address"])
        address.input.textEdited.connect(lambda text: form.__setitem__("address", text))
        address.input.editingFinished.connect(self._load_sources)
        column.addWidget(address)
        column.addSpacing(12)
        fields = {"address": address}
        if form["use_token"]:
            token = Field("Access token",
                          "An access token is a long code made in VCF Operations under your account. Use this only if you "
                          "were given one.", password=True, placeholder="Paste the token here", name="connect-token",
                          value=form["token"])
            token.input.textEdited.connect(lambda text: form.__setitem__("token", text))
            fields["token"] = token
            column.addWidget(token)
            column.addSpacing(8)
            column.addWidget(link("Sign in with a name and password instead", lambda: self._connect_mode(False),
                                  name="connect-use-password"))
        else:
            user = Field("Your user name", placeholder="for example: admin", name="connect-user", value=form["user"])
            user.input.textEdited.connect(lambda text: form.__setitem__("user", text))
            password = Field("Password", "Not saved. You are asked each time you open the app.", password=True,
                             name="connect-password", value=form["password"])
            password.input.textEdited.connect(lambda text: form.__setitem__("password", text))
            fields.update(user=user, password=password)
            column.addLayout(hbox((user, 1), (password, 1), spacing=12, align=Qt.AlignTop))
            column.addSpacing(12)
            column.addWidget(label("Which kind of account is it?", "field-label", wrap=False))
            column.addSpacing(4)
            self.source_combo = QComboBox()
            self.source_combo.setObjectName("connect-source")
            self.source_combo.setAccessibleName("Which kind of account is it?")
            self._fill_sources()
            self.source_combo.currentTextChanged.connect(self._source_chosen)
            column.addWidget(self.source_combo)
            column.addSpacing(3)
            self.source_hint = label(
                "The VCF Operations login page asks the same thing. If you are not sure, leave it as it is.", "hint",
                name="connect-source-hint")
            column.addWidget(self.source_hint)
            column.addSpacing(8)
            column.addWidget(link("I have an access token instead", lambda: self._connect_mode(True),
                                  name="connect-use-token"))
        self.connect_fields = fields

        again = ""
        if form["tries"] > 1 and form["error"] in ("unreachable", "signin"):
            again = f'<br><span style="font-weight:400">Tried again at {esc(form["tried_at"])}: still the same.</span>'
        host = esc(normalize_address(form["address"]))
        if form["error"] == "unreachable":
            column.addSpacing(10)
            column.addWidget(Banner("bad", f"<b>Could not reach {host}.</b><br>Check the address is right, and that this "
                                    f"computer is on the office network or VPN. Then press Connect again.{again}",
                                    name="connect-error"))
        elif form["error"] == "signin":
            what = ("VCF Operations did not accept that access token." if form["use_token"]
                    else "VCF Operations did not accept that name or password.")
            fix = ("Check that the whole token was pasted and that it has not expired." if form["use_token"] else
                   'Check both and try again. Also check "Which kind of account" above: it must match what you choose '
                   "on the VCF Operations login page.")
            column.addSpacing(10)
            column.addWidget(Banner("bad", f"<b>{what}</b><br>{fix}{again}", name="connect-error"))
        elif form["error"] == "certificate-unreadable":
            column.addSpacing(10)
            column.addWidget(Banner(
                "bad", f"<b>{host} answered, but this computer does not recognise its security certificate, and the app "
                "could not read the certificate to show it to you.</b><br>Press Connect again. If it keeps happening, ask "
                "whoever runs VCF Operations for its certificate file and choose it under Settings, Advanced.",
                name="connect-error"))
        if form["note"]:
            column.addSpacing(10)
            column.addWidget(Banner("", esc(form["note"]), name="connect-note"))
        trusted = self.state_store.trusted_certificate(host_and_port(address_to_url(form["address"]))[0]) if form["address"] else None
        if self.settings.cert_file and self.settings.check_ops:
            column.addSpacing(8)
            column.addWidget(label(f"Using certificate file: <b>{esc(Path(self.settings.cert_file).name)}</b>", "small",
                                   name="connect-cert-file"))
        elif trusted and self.settings.check_ops:
            column.addSpacing(8)
            column.addWidget(label(f"The certificate of {host} is trusted on this computer.", "small",
                                   name="connect-cert-trusted"))
        column.addSpacing(16)
        if form["busy"]:
            column.addWidget(label(f"Connecting to {host}{chr(0x2026)}", "small", name="connect-busy"))
            column.addSpacing(5)
            column.addWidget(busy_bar())
        else:
            go = button("Connect", self.do_connect, "primary big", name="connect-go")
            page.primary = go
            column.addLayout(hbox(go, stretch, link("About this app", self._show_about_dialog, name="connect-about")))
        holder = box(column)
        holder.setFixedWidth(500)
        page.body.addStretch(1)
        page.body.addLayout(hbox(stretch, holder, stretch))
        page.body.addStretch(1)

        def focus() -> None:
            target = fields.get("password") if (form["returning"] and "password" in fields) else None
            if target is None:
                target = next((f for f in fields.values() if not f.text()), None) or fields["address"]
            target.input.setFocus()

        page.on_enter = focus
        return page

    def _fill_sources(self) -> None:
        combo = self.source_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(LOCAL_ACCOUNT)
        names = [n for n in self.form["sources"] if n and n.lower() != "local"]
        if names:
            combo.addItems([f"{COMPANY_ACCOUNT}: {n}" for n in names])
        else:
            combo.addItem(COMPANY_ACCOUNT)
        wanted = self.form["source"]
        index = combo.findText(wanted)
        if index < 0 and wanted.startswith(COMPANY_ACCOUNT) and len(names) == 1:
            index = 1
        combo.setCurrentIndex(max(index, 0))
        self.form["source"] = combo.currentText()
        combo.blockSignals(False)

    def _source_chosen(self, text: str) -> None:
        self.form["source"] = text
        if text.startswith(COMPANY_ACCOUNT):
            self._load_sources()

    def _load_sources(self) -> None:
        """Ask VCF Operations which company logins it offers, once the address is known."""
        address = normalize_address(self.form["address"])
        if not address or self.form["use_token"] or self.form["sources_for"] == address:
            return
        self.form["sources_for"] = address
        conn = self._connection_choices(password=None)

        def work() -> List[str]:
            return self.make_adapter(conn.environment()).list_auth_sources()

        def done(names: List[str]) -> None:
            if normalize_address(self.form["address"]) != address:
                return
            self.form["sources"] = [n for n in names if n and n.lower() != "local"]
            if self.screen == "connect" and not self.form["busy"] and getattr(self, "source_combo", None) is not None \
                    and self.page is not None and self.page.name == "connect" and not self.form["use_token"]:
                try:
                    self._fill_sources()
                except RuntimeError:
                    pass  # the screen was replaced while the answer was on its way

        self.tasks.run(work, done, failed=lambda _exc: None, name="login-sources")

    def _connect_mode(self, use_token: bool) -> None:
        self.form.update(use_token=use_token, error=None, note=None)
        self.show_connect()

    def _auth_source(self) -> Optional[str]:
        """The login source name, 'local', or None when a company login was chosen but none can be named."""
        text = self.form["source"]
        if not text.startswith(COMPANY_ACCOUNT):
            return "local"
        if ":" in text:
            return text.split(":", 1)[1].strip()
        names = self.form["sources"]
        return names[0] if len(names) == 1 else None

    def _connection_choices(self, password: Optional[str]) -> ConnectionChoices:
        form = self.form
        url = address_to_url(form["address"])
        host = host_and_port(url)[0] if url else ""
        trusted = self.state_store.trusted_certificate(host) if host else None
        return ConnectionChoices(
            url=url,
            username=form["user"].strip(),
            password=password,
            token=form["token"].strip() or None,
            use_token=form["use_token"],
            auth_source=self._auth_source() or "local",
            verify_ssl=self.settings.check_ops,
            ca_cert_path=self.settings.cert_file,
            pinned_cert_sha256=(trusted or {}).get("sha256") if not self.settings.cert_file else None,
            agent_verify_ssl=self.settings.check_collector,
        )

    def do_connect(self) -> None:
        form = self.form
        fields = self.connect_fields
        form.update(error=None, note=None)
        missing = False
        if not normalize_address(form["address"]):
            fields["address"].set_error("Enter the address of VCF Operations.")
            missing = True
        if form["use_token"]:
            if not form["token"].strip():
                fields["token"].set_error("Paste your access token.")
                missing = True
        else:
            if not form["user"].strip():
                fields["user"].set_error("Enter your user name.")
                missing = True
            if not form["password"]:
                fields["password"].set_error("Enter your password.")
                missing = True
        if missing:
            for field in fields.values():
                if field.input.property("class") == "bad":
                    field.input.setFocus()
                    break
            return
        form["address"] = normalize_address(form["address"])
        if not form["use_token"] and self._auth_source() is None:
            # A company login was chosen but the app does not yet know which one this VCF Operations offers
            self.form["sources_for"] = None
            self._start_connect(resolve_source=True)
            return
        self._start_connect()

    def _start_connect(self, resolve_source: bool = False) -> None:
        form = self.form
        form["busy"] = True
        self.show_connect()
        conn = self._connection_choices(password=form["password"])

        def work():
            if resolve_source:
                names = [n for n in self.make_adapter(conn.environment()).list_auth_sources() if n and n.lower() != "local"]
                if len(names) != 1:
                    return ("sources", names, None)
                conn.auth_source = names[0]
                form_source = names
            else:
                form_source = None
            env = conn.environment()
            adapter = self.make_adapter(env)
            result = connect(env, adapter, self.fetch_certificate)
            return ("result", result, (adapter, env, form_source))

        self.tasks.run(work, lambda answer: self._connect_answer(conn, answer),
                       failed=lambda exc: self._connect_answer(conn, ("error", str(exc), None)), name="connect")

    def _connect_answer(self, conn: ConnectionChoices, answer: tuple) -> None:
        form = self.form
        form["busy"] = False
        kind, value, extra = answer
        form["tries"] += 1
        form["tried_at"] = format_clock()
        if kind == "sources":
            form["sources"] = value
            form["tries"] = 0
            self.show_connect()
            if value:
                self.source_hint.setText("! VCF Operations offers several company logins. Pick the one you use.")
            else:
                self.source_hint.setText(
                    f'! VCF Operations did not list a company login. Leave this as "{LOCAL_ACCOUNT}", or check the address.')
            self.source_hint.setProperty("class", "hint-bad")
            self.source_hint.style().polish(self.source_hint)
            return
        if kind == "error":
            form["error"] = "unreachable"
            if form.get("last_error") != "unreachable":
                form["tries"] = 1
            form["last_error"] = "unreachable"
            self.logger.warning("Connect failed: %s", value)
            self.show_connect()
            return
        result = value
        adapter, env, sources = extra
        if sources:
            form["sources"] = sources
            form["source"] = f"{COMPANY_ACCOUNT}: {sources[0]}"
        if result.outcome == ConnectOutcome.UNREACHABLE:
            form["error"] = "unreachable"
        elif result.outcome == ConnectOutcome.UNAUTHORIZED:
            form["error"] = "signin"
        elif result.outcome == ConnectOutcome.UNTRUSTED:
            if result.certificate is None:
                form["error"] = "certificate-unreadable"
            else:
                form["error"] = "cert"
                form["cert"] = result.certificate
                form["cert_changed"] = result.certificate_changed
                if conn.ca_cert_path and not conn.pinned_cert_sha256:
                    form["cert_note"] = (
                        f'"{Path(conn.ca_cert_path).name}" is not the certificate that {normalize_address(conn.url)} is '
                        "using, so it cannot be used to check it. Choose a different file, or check the details above instead.")
        else:
            form.update(error=None, cert=None, cert_note=None, tries=0, password="", token="")
            form["last_error"] = None
            self._on_connected(conn, adapter, env)
            return
        if form.get("last_error") != form["error"]:
            form["tries"] = 1  # "tried again: still the same" only follows the same answer twice
        form["last_error"] = form["error"]
        self.show_connect()

    def _certificate_page(self) -> Page:
        form = self.form
        cert: PresentedCertificate = form["cert"]
        host = esc(normalize_address(form["address"]))
        who = "your access token" if form["use_token"] else esc(form["user"])
        page = Page("certificate", margins=(22, 18, 22, 18), spacing=0)
        column = QVBoxLayout()
        column.setSpacing(0)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.brand())
        column.addWidget(label("One check before connecting", "h1-big", name="page-title"))
        column.addWidget(label(f"Connecting to <b>{host}</b> as <b>{who}</b>.", "sub", name="page-sub"))
        column.addSpacing(10)
        if form["cert_changed"]:
            head = (f"<b>The security certificate of {host} has changed since you trusted it.</b><br>"
                    '<span style="font-weight:400">That happens when a certificate is renewed, but it can also mean '
                    "something is in the way. This is the one the server shows now:</span>")
            trust_text = "Trust the new certificate and connect"
        else:
            head = (f"<b>This computer has not seen the security certificate of {host} before.</b><br>"
                    '<span style="font-weight:400">That is normal when a company issues its own certificates. This is the '
                    "one the server showed:</span>")
            trust_text = "Trust this certificate and connect"
        details = (
            '<table cellspacing="0" cellpadding="1" style="margin-top:6px;margin-bottom:6px">'
            f'<tr><td style="padding-right:14px">Issued to</td><td><b>{esc(cert.issued_to)}</b></td></tr>'
            f'<tr><td style="padding-right:14px">Issued by</td><td><b>{esc(cert.issued_by)}</b></td></tr>'
            f'<tr><td style="padding-right:14px">Valid until</td><td><b>{esc(cert.valid_until)}</b></td></tr></table>')
        advice = ('Two things to look at: "Issued to" should be the address you typed, and "Issued by" should be your own '
                  "company or its IT team. If both are right, trust it; the app remembers it and warns you if it ever "
                  "changes. If you are not sure, press Cancel and ask whoever runs VCF Operations.")
        text = label(f'{head}{details}<span style="font-weight:400">{advice}</span>', "banner-text", name="certificate-details")
        trust = button(trust_text, self._trust_certificate, "primary", name="certificate-trust")
        page.primary = trust
        inner = vbox(text, hbox(trust, button("Cancel", self._cancel_certificate, name="certificate-cancel"), stretch),
                     link("I was given a certificate file by my IT team", self._pick_certificate_file,
                          name="certificate-file"), spacing=10, margins=(12, 10, 12, 10))
        if form["cert_note"]:
            inner.addWidget(label("! " + esc(form["cert_note"]), "hint-bad", name="certificate-note"))
        banner = QFrame()
        banner.setProperty("class", "banner banner-warn")
        banner.setLayout(inner)
        column.addWidget(banner)
        holder = box(column)
        holder.setFixedWidth(500)
        page.body.addStretch(1)
        page.body.addLayout(hbox(stretch, holder, stretch))
        page.body.addStretch(1)
        page.on_enter = trust.setFocus
        return page

    def _trust_certificate(self) -> None:
        cert: PresentedCertificate = self.form["cert"]
        host = host_and_port(address_to_url(self.form["address"]))[0]
        self.state_store.trust_certificate(host, {
            "sha256": cert.sha256, "issued_to": cert.issued_to, "issued_by": cert.issued_by, "valid_until": cert.valid_until,
        })
        if self.settings.cert_file:
            self.settings.cert_file = None
            self.settings.save(self.state_store)
        self.form.update(error=None, cert=None, cert_note=None, tries=0)
        self._start_connect()

    def _cancel_certificate(self) -> None:
        self.form.update(error=None, cert=None, cert_note=None, tries=0,
                         note="Not connected: you cancelled at the certificate check. Nothing was sent.")
        self.show_connect()

    def choose_certificate_file(self) -> Optional[str]:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose your company's certificate file", self.settings.cert_file or "",
            "Certificate files (*.pem *.crt *.cer);;All files (*)")
        return path or None

    def _pick_certificate_file(self) -> None:
        path = self.choose_certificate_file()
        if not path:
            return
        self.settings.cert_file = path
        self.settings.save(self.state_store)
        self.form.update(error=None, cert_note=None, tries=0)
        self.toast(f"Using the certificate file {Path(path).name}.")
        self._start_connect()

    def _on_connected(self, conn: ConnectionChoices, adapter: VCFOpsIntegration, env: VCFEnvironment) -> None:
        self.conn = conn
        self.adapter = adapter
        self.connected = True
        self.session.clear()
        try:
            self.state_store.save_environment(env.model_copy(update={"name": "default"}))
            self.state_store.save_preference("vcf_auth_mode", "token" if conn.use_token else "password")
        except Exception:
            self.logger.exception("Could not remember the connection")
        self.form["returning"] = True
        self.filter, self.query, self.selection = "all", "", []
        self.reload_machines(announce="connected")

    def sign_out(self) -> None:
        """Back to the Connect screen, keeping the address and name but nothing else."""
        if self.flow is not None:
            self.flow.abandon()
            self.flow = None
        self.connected = False
        self.conn = None
        self.adapter = None
        self.machines, self.collectors = [], []
        self.session.clear()
        self.form.update(returning=False, error=None, note=None, password="", token="", tries=0, cert=None)
        self.show_connect()

    # ------------------------------------------------------------------ machines
    def machine(self, machine_id: Optional[str]) -> Optional[Machine]:
        return next((m for m in self.machines if m.id == machine_id), None)

    def listed(self) -> List[Machine]:
        """Machines that count: VMware's own system machines never do, powered-off ones only when shown."""
        return [m for m in self.machines if not m.infra and (self.show_off or not m.off)]

    def refresh_statuses(self) -> None:
        """Work out each machine's status from VCF Operations, the app's history and the takeover records."""
        records = {}
        try:
            records = {(r.vc_id, r.vm_mor): r.state for r in self.journal.list_records()}
        except Exception:
            self.logger.exception("Could not read the takeover records")
        for machine in self.machines:
            state = records.get((machine.vm.vc_id, machine.vm.vm_mor))
            found = machine_status(machine.vm, self.history.last_change(machine.id), state)
            if found.status == STATUS_WAITING and machine.id in self.arrived:
                found.status = STATUS_SENDING
            machine.status, machine.problem, machine.last = found.status, found.problem, found.last

    def reload_machines(self, announce: Optional[str] = None, then: Optional[Callable[[], None]] = None,
                        quiet: bool = False) -> None:
        """Read the machines and collectors from VCF Operations again.

        quiet keeps the current screen and the current list in place until the new list arrives.
        """
        if self.adapter is None:
            return
        waiting_before = {m.id for m in self.machines if m.status == STATUS_WAITING}
        if not quiet:
            self.loading, self.load_error = True, None
            self.show_machines()
        adapter = self.adapter

        def work():
            vms = adapter.list_virtual_machines(strict=True)
            try:
                collectors = adapter.list_collector_targets()
            except Exception as exc:
                self.logger.warning("Could not read the collectors: %s", exc)
                collectors = []
            return vms, collectors, getattr(adapter, "inventory_warning", None), getattr(adapter, "platform_notice", None)

        def done(answer) -> None:
            vms, collectors, warning, platform = answer
            self.platform_note = platform
            self.loading = False
            fresh = [Machine(vm=vm) for vm in sorted(vms, key=lambda v: v.name.lower())]
            if self.flow is not None:
                # A path in progress keeps the machines it is working on, even if VCF Operations stops listing one
                listed = {m.id for m in fresh}
                wanted = set(self.flow.ids) | set(self.flow.dropped)
                fresh += [m for m in self.machines if m.id in wanted and m.id not in listed]
            self.machines = fresh
            self.collectors = collectors
            self.inventory_note = warning
            self.arrived &= {m.id for m in self.machines}
            self.refresh_statuses()
            self.selection = [i for i in self.selection if self.machine(i) is not None and self.machine(i).selectable]
            if announce == "connected":
                host = esc(normalize_address(self.conn.url))
                count = len([m for m in self.machines if not m.infra and not m.off])
                self.banner = ("ok", f"<b>Connected to {host}. The app is ready.</b> It found {plural(count, 'machine')}. "
                               'To start, press "Set up" at the end of a machine\'s row, or tick several machines to do '
                               "them together.")
            else:
                arrived = [m for m in self.machines if m.id in waiting_before and m.status == STATUS_SENDING]
                if arrived:
                    self.banner = ("ok", f"<b>First data has arrived from {plural(len(arrived), 'machine')}.</b> "
                                   f"{'It is' if len(arrived) == 1 else 'They are'} now shown as \"Sending data\".")
            if then is not None:
                then()
            if self.screen == "machines" and self.flow is None:
                self.show_machines()
            elif self.flow is None:
                self.render_side()

        def failed(exc: Exception) -> None:
            self.loading = False
            self.logger.warning("Could not read the machines: %s", exc)
            if quiet:
                return  # the list on screen stays as it was; Refresh list tries again
            self.load_error = str(exc)
            if then is not None:
                then()
            if self.screen == "machines" and self.flow is None:
                self.show_machines()

        self.tasks.run(work, done, failed, name="inventory")

    # ------------------------------------------------------------------ navigation
    def show_machines(self, banner: Optional[tuple] = None) -> None:
        from vcf_ops_telegraf_helper.gui.screens_machines import machines_page

        if banner is not None:
            self.banner = banner
        self.machine_id = None
        self.set_page(machines_page(self), "machines")

    def show_machine(self, machine_id: str) -> None:
        from vcf_ops_telegraf_helper.gui.screens_machines import machine_page

        if self.machine(machine_id) is None:
            self.show_machines()
            return
        self.machine_id = machine_id
        self.selection = []
        self.set_page(machine_page(self, self.machine(machine_id)), "machine")

    def show_activity(self) -> None:
        from vcf_ops_telegraf_helper.gui.screens_more import activity_page

        self.selection = []
        self.entry_from_machine = None
        self.set_page(activity_page(self), "activity")

    def show_entry(self, entry_id: str, from_machine: Optional[str] = None) -> None:
        from vcf_ops_telegraf_helper.gui.screens_more import entry_page

        entry = self.history.get(entry_id)
        if entry is None:
            self.show_activity()
            return
        self.entry_from_machine = from_machine if self.machine(from_machine) is not None else None
        self.set_page(entry_page(self, entry), "entry")

    def show_settings(self, tab: str = "conn") -> None:
        from vcf_ops_telegraf_helper.gui.screens_more import settings_page

        self.selection = []
        self.set_page(settings_page(self, tab), "settings")

    def start_flow(self, kind: str, ids: List[str], retry: bool = False, resume: bool = False) -> None:
        from vcf_ops_telegraf_helper.gui.flow import Flow

        machines = [self.machine(i) for i in ids]
        if not ids or any(m is None for m in machines):
            return
        origin = (self.screen, self.machine_id, getattr(self.page, "entry_id", None))
        self.selection = []
        self.banner = None
        self.flow = Flow(self, kind, list(ids), retry=retry, resume=resume, origin=origin)
        self.flow.start()

    def leave_flow(self) -> None:
        self.flow = None
