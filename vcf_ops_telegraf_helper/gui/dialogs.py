"""The small dialogs: one question each, with the blue button last."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QFileDialog, QFrame, QPlainTextEdit, QScrollArea, QWidget

from vcf_ops_telegraf_helper.gui.model import Machine, SignIn
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
    mark,
    stretch,
    vbox,
)
from vcf_ops_telegraf_helper.models.endpoint import ConnectionMethod, EndpointTarget, OSFamily
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo
from vcf_ops_telegraf_helper.models.workflow import StageStatus, UninstallOptions
from vcf_ops_telegraf_helper.storage.history import ActivityRow
from vcf_ops_telegraf_helper.workflow.batch import remove_entry
from vcf_ops_telegraf_helper.workflow.checks import collector_kind, collector_label, same_collector
from vcf_ops_telegraf_helper.workflow.collect import DATABASE_PORTS, name_of
from vcf_ops_telegraf_helper.workflow.plain import COLLECTOR_IS, join_names, plural, stage_log
from vcf_ops_telegraf_helper.workflow.plan import MachinePlan, build_remove_command
from vcf_ops_telegraf_helper.workflow.uninstall import UninstallEndpointWorkflow


def show_text(parent: QWidget, title: str, text: str, intro: str = "", name: str = "text-dialog") -> None:
    """A wide dialog of detail for people who want it: a log, the settings files, the licences."""
    dialog = Dialog(parent, title, intro, width=640, name=name)
    view = QPlainTextEdit()
    view.setObjectName("text-dialog-text")
    view.setReadOnly(True)
    view.setProperty("class", "code-block")
    view.setPlainText(text)
    view.setMinimumHeight(330)
    dialog.add(view)

    def copy() -> None:
        QApplication.clipboard().setText(text)
        copied.setText("Copied")

    copied = dialog.add_button("Copy", copy, name="text-dialog-copy")
    dialog.add_button("Close", dialog.accept, "primary", name="text-dialog-close")
    dialog.exec()


def choose_key_file(parent: QWidget) -> Optional[str]:
    path, _ = QFileDialog.getOpenFileName(parent, "Choose your key file", str(Path.home() / ".ssh"))
    return path or None


class KeyOrPassword(QWidget):
    """The second half of a sign-in: a password, or for Linux a key file instead."""

    def __init__(self, parent_window: QWidget, signin: SignIn, allow_key: bool, prefix: str):
        super().__init__()
        self.setProperty("class", "clear")
        self.window_ref = parent_window
        self.signin = signin
        self.allow_key = allow_key
        self.password = Field("Password", password=True, name=f"{prefix}-password", value=signin.password)
        self.password.input.textEdited.connect(lambda text: setattr(self.signin, "password", text))
        self.key_caption = label("Key file", "field-label", wrap=False)
        self.key_shown = label("", "readonly-field", wrap=False, name=f"{prefix}-key-file")
        self.key_choose = button("Choose…", self._choose, name=f"{prefix}-key-choose")
        self.key_error = label("", "hint-bad", name=f"{prefix}-key-error")
        self.key_error.hide()
        self.key_box = box(vbox(self.key_caption, hbox((self.key_shown, 1), self.key_choose, spacing=8), self.key_error,
                                spacing=4))
        self.setLayout(vbox(self.password, self.key_box, spacing=0))
        self.refresh()

    def refresh(self) -> None:
        use_key = self.allow_key and self.signin.use_key
        self.password.setVisible(not use_key)
        self.key_box.setVisible(use_key)
        self.key_shown.setText(esc(self.signin.key_file or "No file chosen"))

    def _choose(self) -> None:
        path = self.window_ref.choose_key_file() if hasattr(self.window_ref, "choose_key_file") else choose_key_file(self)
        if path:
            self.signin.key_file = path
            self.key_error.hide()
            self.refresh()

    def set_error(self, message: Optional[str]) -> None:
        if self.signin.use_key and self.allow_key:
            self.key_error.setText("! " + esc(message) if message else "")
            self.key_error.setVisible(bool(message))
        else:
            self.password.set_error(message)


def sign_in_dialog(win, machine: Machine, allow_key: bool = True, other_machines: bool = True) -> Optional[SignIn]:
    """Ask for a sign-in for one machine. Returns it, or None when cancelled."""
    intro = ("Used for this machine only. The other machines keep the sign-in you already gave."
             if other_machines else "The app signs in to the machine with this. It is remembered until you close the "
             "app, and never saved to disk.")
    dialog = Dialog(win, f"Sign-in for {machine.name}", intro, name="signin-dialog")
    signin = SignIn()
    user = Field(f"User name on {machine.name}", name="signin-dialog-user")
    user.input.textEdited.connect(lambda text: setattr(signin, "user", text))
    secret = KeyOrPassword(win, signin, allow_key and machine.os == "linux", "signin-dialog")
    error = label("", "hint-bad", name="signin-dialog-error")
    error.hide()
    dialog.add(user, secret, error)
    if allow_key and machine.os == "linux":
        toggle = link("Use a key file instead of a password", name="signin-dialog-key-toggle")

        def flip() -> None:
            signin.use_key = not signin.use_key
            toggle.setText("Use a password instead" if signin.use_key else "Use a key file instead of a password")
            error.hide()
            secret.refresh()

        toggle.clicked.connect(lambda _c=False: flip())
        dialog.add(hbox(toggle, stretch))
    chosen: List[SignIn] = []

    def accept() -> None:
        if not signin.complete:
            error.setText("! Enter a user name and choose the key file." if signin.use_key
                          else "! Enter both a user name and a password.")
            error.show()
            if not signin.user.strip():
                user.set_error("Enter a user name.")
            return
        chosen.append(signin)
        dialog.accept()

    dialog.add_button("Cancel", dialog.reject, name="signin-dialog-cancel")
    dialog.add_button("Test this sign-in" if other_machines else "Sign in", accept, "primary", name="signin-dialog-ok")
    user.input.setFocus()
    dialog.exec()
    return chosen[0] if chosen else None


def collector_dialog(
    win,
    names: Sequence[str],
    choices: Sequence[tuple],
    current: Optional[CollectorInfo] = None,
) -> Optional[CollectorInfo]:
    """Pick where data goes. `choices` is (collector, the machines that cannot reach it), already tested."""
    one = len(names) == 1
    who = esc(names[0]) if one else "these machines"
    dialog = Dialog(
        win, f"Where should {names[0] if one else 'these machines'} send {'its' if one else 'their'} data?",
        f"{COLLECTOR_IS} The app has tested each one from {'this machine' if one else 'these machines'}.",
        name="collector-dialog")
    picked: List[CollectorInfo] = []

    def use(collector: CollectorInfo) -> None:
        picked.append(collector)
        dialog.accept()

    for index, (collector, cannot) in enumerate(choices):
        shown = collector_label(collector)
        text = label(esc(shown[0].upper() + shown[1:]), "h2")
        kind = label(esc(collector_kind(collector)), "small")
        if cannot:
            reach = label(f'{mark("bad")} {esc(join_names(list(cannot))) if not one else "This machine"} cannot reach it',
                          "small", name=f"collector-reach-{index}")
        else:
            reach = label(f'{mark("ok")} {"This machine" if one else "Every machine in this run"} can reach it', "small",
                          name=f"collector-reach-{index}")
        line = hbox((box(vbox(text, kind, reach, spacing=1)), 1), spacing=10, margins=(0, 8, 0, 8))
        if same_collector(collector, current):
            line.addWidget(label("In use now", "small", wrap=False))
        elif not cannot:
            line.addWidget(button("Use this one", lambda c=collector: use(c), "primary sm", name=f"collector-use-{index}"))
        holder = QFrame()
        holder.setProperty("class", "setrow" if index < len(choices) - 1 else "setrow-last")
        holder.setLayout(line)
        dialog.add(holder)
    if not choices:
        dialog.add(label(f"VCF Operations has not listed any collector for {who}.", "plain"))
    dialog.add_button("Cancel", dialog.reject, name="collector-cancel")
    dialog.exec()
    return picked[0] if picked else None


def confirm_remove(win, machine: Machine) -> bool:
    dialog = Dialog(win, f"Remove monitoring from {machine.name}?", name="remove-dialog")
    dialog.add(label(
        "This signs in to the machine, stops Telegraf and uninstalls it, and deletes the settings this app put there. "
        "The machine stops sending detailed measurements. Its record and history in VCF Operations stay.<br><br>"
        "You can set monitoring up again at any time.", "plain"))
    dialog.add_button("Keep monitoring", dialog.reject, name="remove-cancel")
    dialog.add_button("Remove monitoring", dialog.accept, "danger", name="remove-ok")
    return dialog.exec() == Dialog.Accepted


def remove_monitoring(win, machine: Machine) -> None:
    """Uninstall Telegraf from the machine, show progress, and record what happened."""
    signin = win.session.for_machine(machine)
    if signin is None or not signin.complete:
        signin = sign_in_dialog(win, machine, other_machines=False)
        if signin is None:
            return
        win.session.machine[machine.id] = signin
    facts = win.history.facts(machine.id)
    windows = machine.os == "windows"
    ssl = bool(facts.get("winrm_ssl")) if windows else False
    port = int(facts.get("port") or ((5986 if ssl else 5985) if windows else win.settings.ssh_port))
    plan = MachinePlan(
        machine_id=machine.id, name=machine.name, address=machine.address, os=machine.os, username=signin.user.strip(),
        password=signin.password, key_file=signin.key_file if signin.use_key else None, port=port, winrm_ssl=ssl)
    target = EndpointTarget(
        hostname=machine.address, os_family=OSFamily.WINDOWS if windows else OSFamily.LINUX,
        connection_method=ConnectionMethod.WINRM if windows else ConnectionMethod.SSH, port=port,
        username=plan.username, password=None if plan.key_file else plan.password, key_filename=plan.key_file,
        winrm_use_ssl=ssl)

    page = Page("removing")
    page.title(f"Removing monitoring from {esc(machine.name)}…",
               "Signing in, stopping Telegraf, and removing it and its settings. Keep this app open.")
    page.add(busy_bar(420)).end()
    # Nothing else can be started, and the app cannot be closed, until the removal has ended and is recorded
    win.working_on = machine.id
    win.set_page(page, "machine")

    def work():
        executor = win.make_executor(target)
        try:
            return UninstallEndpointWorkflow(target=target, executor=executor,
                                             options=UninstallOptions(purge_packages=True, purge_repositories=True)).run()
        finally:
            try:
                executor.close()
            except Exception:
                pass

    def record(ok: bool, msg: str, todo: str, log: str) -> None:
        row = ActivityRow(machine_id=machine.id, name=machine.name, os=machine.os, ok=ok, msg=msg, todo=todo,
                          cmd=build_remove_command(plan))
        win.history.add(remove_entry(row, log))
        if ok:
            win.history.remember(machine.id, collecting=None, collector=None, telegraf=None)
            win.arrived.discard(machine.id)
        win.refresh_statuses()

    def done(summary) -> None:
        win.working_on = None
        log = stage_log(machine.name, summary.stages,
                        extra=[f"{k:<26}: {v}" for k, v in summary.verifications.items()])
        if summary.success:
            record(True, "Telegraf and its settings were removed from the machine.", "", log)
            win.show_machines(("ok", f"<b>Monitoring was removed from {esc(machine.name)}.</b> It is listed as "
                               '"Not set up" again.'))
            return
        failed = next((s for s in summary.stages if s.status == StageStatus.FAIL), None)
        connect = failed is not None and "Connect" in str(getattr(failed.stage, "value", failed.stage))
        what = ("The app could not sign in to the machine, so nothing was removed." if connect
                else "Telegraf could not be removed completely. Part of it may still be on the machine.")
        todo = ("Check the sign-in and that the machine is switched on, then press Remove monitoring again." if connect
                else "Press Remove monitoring again. If it happens again, open this result under Activity and send its "
                     "Technical details to whoever supports this app.")
        if connect:
            win.session.machine.pop(machine.id, None)
        record(False, what, todo, log)
        win.show_machine(machine.id)
        win.page.body.insertWidget(1, Banner("bad", f"<b>{esc(what)}</b><br>{esc(todo)}", name="remove-error"))
        adopt(win.page)

    def failed(exc: Exception) -> None:
        win.working_on = None
        record(False, "The app could not start removing monitoring from the machine.",
               "Press Remove monitoring again.", f"== {machine.name} ==\n{exc}")
        win.show_machine(machine.id)
        win.page.body.insertWidget(1, Banner("bad", "<b>The app could not start removing monitoring from the machine."
                                             "</b><br>Press Remove monitoring again.", name="remove-error"))

    win.tasks.run(work, done, failed, name="remove")


# ---------------------------------------------------------------------------------------------
# What to collect: the questions behind a tick box
# ---------------------------------------------------------------------------------------------
def database_dialog(win, key: str, found: bool, test: Callable[[str, str, int], List[str]],
                    current: Optional[dict] = None) -> Optional[dict]:
    """Ask for a database account, try it, and say what it found. Returns the answer or None."""
    title = name_of(key)
    dialog = Dialog(win, title, name="database-dialog")
    current = current or {}
    lead = f"The app found {title} running. " if found else ""
    dialog.add(label(
        f"{lead}To read how busy it is, Telegraf needs a database account. Any account that can sign in to the database "
        "will do; ask whoever looks after the database for a read-only one.", "small"))
    user = Field("Database user name", placeholder="for example: monitor", name="database-user",
                 value=str(current.get("user") or ""))
    password = Field("Password", password=True, name="database-password")
    dialog.add(hbox((user, 1), (password, 1), spacing=12, align=Qt.AlignTop))
    status = QWidget()
    status.setProperty("class", "clear")
    status_layout = vbox(spacing=6)
    status.setLayout(status_layout)
    dialog.add(status)
    answer: List[dict] = []
    state = {"databases": None, "tested": False, "alive": True}
    dialog.finished.connect(lambda _code: state.__setitem__("alive", False))

    def show(widget: Optional[QWidget]) -> None:
        from vcf_ops_telegraf_helper.gui.widgets import _empty

        _empty(status_layout)
        if widget is not None:
            status_layout.addWidget(widget)
            adopt(status)

    def details(databases: List[str]) -> dict:
        return {"user": user.text().strip(), "password": password.text(), "port": DATABASE_PORTS[key],
                "databases": databases}

    def run_test() -> None:
        if not user.text().strip() or not password.text():
            # Each empty box says what is missing, once, right under it
            show(None)
            if not user.text().strip():
                user.set_error("Enter the user name.")
            if not password.text():
                password.set_error("Enter the password.")
            return
        test_button.setEnabled(False)
        show(busy_bar())
        name, secret = user.text().strip(), password.text()

        def done(databases: List[str]) -> None:
            if not state["alive"]:
                return
            test_button.setEnabled(True)
            state["tested"] = True
            if databases:
                state["databases"] = databases
                shown = join_names(databases[:4]) + ("" if len(databases) <= 4 else f" and {len(databases) - 4} more")
                show(Banner("ok", f"<b>Connected.</b> Found {plural(len(databases), 'database')}: {esc(shown)}. "
                            f"{'It' if len(databases) == 1 else 'All ' + str(len(databases))} will be monitored.",
                            name="database-found"))
                test_button.hide()
                add_button.show()
                add_button.setDefault(True)
                anyway.hide()
            else:
                show(label(
                    "! The app could not list the databases with that account. Check the user name and the password, "
                    "then test again. If you are sure they are right, you can add it anyway: the result of the setup "
                    "says whether Telegraf could read the database.", "hint-bad", name="database-error"))
                anyway.show()

        win.tasks.run(lambda: test(name, secret, DATABASE_PORTS[key]), done,
                      failed=lambda _exc: done([]), name="database-test")

    def add() -> None:
        answer.append(details(state["databases"] or []))
        dialog.accept()

    def typed(_text: str) -> None:
        # What was said about the last test no longer describes what is in the boxes
        if state["tested"] or state["databases"]:
            state["tested"], state["databases"] = False, None
            show(None)
            anyway.hide()
            add_button.hide()
            test_button.show()
            test_button.setDefault(True)

    user.input.textEdited.connect(typed)
    password.input.textEdited.connect(typed)
    dialog.add_button("Cancel", dialog.reject, name="database-cancel")
    anyway = dialog.add_button("Add anyway", add, name="database-anyway")
    anyway.hide()
    add_button = dialog.add_button("Add", add, "primary", name="database-add")
    add_button.hide()
    test_button = dialog.add_button("Test and find databases", run_test, "primary", name="database-test")
    user.input.setFocus()
    dialog.exec()
    return answer[0] if answer else None


def ping_dialog(win, current: str = "") -> Optional[str]:
    dialog = Dialog(win, name_of("ping"), name="ping-dialog")
    address = Field("Address to ping from each machine", "A name or an address that should always answer, such as your "
                    "gateway.", placeholder="for example: gateway.example.com or 10.20.0.1", name="ping-address",
                    value=current)
    dialog.add(address)
    answer: List[str] = []

    def accept() -> None:
        if not address.text().strip():
            address.set_error("Enter an address to ping.")
            return
        answer.append(address.text().strip())
        dialog.accept()

    dialog.add_button("Cancel", dialog.reject, name="ping-cancel")
    dialog.add_button("OK", accept, "primary", name="ping-ok")
    address.input.setFocus()
    dialog.exec()
    return answer[0] if answer else None


def web_dialog(win, key: str, found_on: Sequence[str], missing_on: Sequence[str]) -> bool:
    """Tell the person what the app found about a web server's status page. True to switch it on."""
    title = name_of(key)
    dialog = Dialog(win, title, name="web-dialog")
    dialog.add(label(f"{title} has a built-in status page that Telegraf reads. The app looked for it at the usual place.",
                     "small"))
    if found_on and not missing_on:
        dialog.add(Banner("ok", f"<b>Found it</b> on the machines that run {esc(title)}. Nothing to fill in.",
                          name="web-found"))
    elif found_on:
        dialog.add(Banner("warn", f"<b>Found it</b> on {esc(join_names(list(found_on)))}, but not on "
                          f"{esc(join_names(list(missing_on)))}.", name="web-found"))
    elif missing_on:
        dialog.add(Banner("warn", f"<b>Not found</b> on {esc(join_names(list(missing_on)))}: the status page is switched "
                          "off there.", name="web-found"))
    else:
        dialog.add(Banner("", f"The app did not find {esc(title)} running on a machine in this run, so it has not looked "
                          "for the page yet.", name="web-found"))
    dialog.add(label("Machines where it is switched off are reported after setup, with how to switch it on.", "small"))
    dialog.add_button("Cancel", dialog.reject, name="web-cancel")
    dialog.add_button("OK", dialog.accept, "primary", name="web-ok")
    return dialog.exec() == Dialog.Accepted


def pick_list_dialog(win, title: str, intro: str, fetch: Callable[[], List[tuple]], selected: Sequence[str] = (),
                     empty: str = "", name: str = "pick-dialog") -> Optional[List[str]]:
    """Read a list from the machines and let the person tick entries. `fetch` returns (key, words) pairs."""
    dialog = Dialog(win, title, name=name)
    dialog.add(label(intro, "small"))
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setProperty("class", "clear")
    area.setMinimumHeight(170)
    area.setMaximumHeight(300)
    inner = QWidget()
    inner.setProperty("class", "clear")
    rows = vbox(busy_bar(), spacing=4)
    rows.addStretch(1)
    inner.setLayout(rows)
    area.setWidget(inner)
    dialog.add(area)
    ticks: List[QCheckBox] = []
    answer: List[List[str]] = []
    state = {"alive": True}
    dialog.finished.connect(lambda _code: state.__setitem__("alive", False))

    def fill(items: List[tuple]) -> None:
        if not state["alive"]:
            return
        from vcf_ops_telegraf_helper.gui.widgets import _empty

        _empty(rows)
        if not items:
            rows.addWidget(label(empty or "Nothing was found on the machines.", "small", name=f"{name}-empty"))
        for key, words in items:
            tick = QCheckBox(words)
            tick.setObjectName(f"{name}-{key}")
            tick.setProperty("key", key)
            tick.setChecked(key in selected)
            ticks.append(tick)
            rows.addWidget(tick)
        rows.addStretch(1)
        adopt(inner)
        ok.setEnabled(bool(items))

    def accept() -> None:
        answer.append([str(t.property("key")) for t in ticks if t.isChecked()])
        dialog.accept()

    dialog.add_button("Cancel", dialog.reject, name=f"{name}-cancel")
    ok = dialog.add_button("OK", accept, "primary", name=f"{name}-ok")
    ok.setEnabled(False)
    win.tasks.run(fetch, fill, failed=lambda _exc: fill([]), name="pick-list")
    dialog.exec()
    return answer[0] if answer else None


def custom_dialog(win, text: str = "") -> Optional[str]:
    """For people who already write Telegraf settings by hand. Returns the text, or None when cancelled."""
    from vcf_ops_telegraf_helper.validation.validator import Validator

    dialog = Dialog(win, "Your own Telegraf settings",
                    "For people who already write Telegraf settings by hand. Anything you paste is added as it is, after "
                    "being checked for mistakes.", width=640, name="custom-dialog")
    edit = QPlainTextEdit()
    edit.setObjectName("custom-text")
    edit.setProperty("class", "code-block")
    edit.setPlaceholderText("Paste Telegraf input settings here")
    edit.setPlainText(text)
    edit.setMinimumHeight(170)
    error = label("", "hint-bad", name="custom-error")
    error.hide()
    dialog.add(edit, error)
    answer: List[str] = []

    def accept() -> None:
        value = edit.toPlainText().strip()
        if value:
            verdict = Validator.validate_toml_syntax(value, "Your settings")
            if not verdict.is_valid:
                error.setText("! These are not valid Telegraf settings: " + esc(str(verdict.details or verdict.message)))
                error.show()
                return
        answer.append(value)
        dialog.accept()

    dialog.add_button("Cancel", dialog.reject, name="custom-cancel")
    dialog.add_button("Use these", accept, "primary", name="custom-ok")
    dialog.exec()
    return answer[0] if answer else None


def accept_endpoint_dialog(win, name: str, checks: Sequence[str]) -> bool:
    """VCF Operations has not confirmed it removed its agent; the machine itself looks clean. Carry on?"""
    dialog = Dialog(win, "VCF Operations has not confirmed the removal", name="accept-dialog")
    found = "".join(f"<br>&bull; {esc(check)}" for check in checks)
    dialog.add(label(
        f"VCF Operations has still not said that it finished removing its own agent from <b>{esc(name)}</b>. "
        f"The app looked at the machine itself and found:{found}<br><br>If you carry on, open-source Telegraf is "
        "installed now. If you stop, nothing more is done and you can finish the takeover later.", "plain"))
    typed = Field(f"To carry on, type the machine's name: {name}", name="accept-typed")
    dialog.add(typed)
    result = {"ok": False}

    def accept() -> None:
        if typed.text().strip() != name:
            typed.set_error(f"That does not match. Type {name} exactly.")
            return
        result["ok"] = True
        dialog.accept()

    dialog.add_button("Stop here", dialog.reject, name="accept-stop")
    dialog.add_button("Carry on", accept, "primary", name="accept-ok")
    dialog.exec()
    return result["ok"]
