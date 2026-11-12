"""The window, driven the way a person drives it: real clicks and typing on real widgets.

Each test does one of the jobs the screens exist for, start to finish, against the stand-ins in
gui_standins. The rule under test throughout: the app never reports success for something it
did not do. Results count against what was picked, everything not done is named, and a setting
is shown at work in the place it applies.
"""

from __future__ import annotations

import html
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QPushButton

    import gui_standins as standins
    from gui_driver import Driver
    from vcf_ops_telegraf_helper.gui.main_window import MainWindow
    from vcf_ops_telegraf_helper.gui.theme import build_stylesheet
    from vcf_ops_telegraf_helper.storage.state import StateStore
except (ImportError, OSError) as exc:
    pytest.skip(f"PySide6 GUI tests skipped (missing library or display dependency: {exc})", allow_module_level=True)

FAST = {"uninstall_poll_seconds": 0, "continuity_poll_seconds": 0, "continuity_wait_seconds": 20}


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    font = app.font()
    if font.pointSize() <= 0:
        font.setPointSize(9)
        app.setFont(font)
    return app


class Session:
    """A window on the stand-ins, with the driver, closed again at the end of the test."""

    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.open: list = []

    def start(self, name: str = "home", connect: bool = True, world=None, seed: bool = True):
        window, world = standins.make_window(self.tmp_path / name, world=world, seed=seed, waits=FAST)
        window.show()
        driver = Driver(window, self.tmp_path / "shots")
        driver.settle()
        self.open.append(window)
        if connect:
            if not window.form["address"]:
                driver.type("connect-address", "ops.example.com")
                driver.type("connect-user", "admin")
            driver.type("connect-password", "pw")
            driver.click("connect-go")
            assert driver.page() == "machines"
        return driver, window, world

    def close(self) -> None:
        for window in self.open:
            window.close()


@pytest.fixture
def session(qapp, tmp_path):
    found = Session(tmp_path)
    yield found
    found.close()
    qapp.processEvents()


def sign_in(d: Driver, linux=("svc-monitor", "pw"), windows=("administrator", "pw")) -> None:
    if d.has("signin-user-linux"):
        d.type("signin-user-linux", linux[0])
        d.type("signin-linux-password", linux[1])
    if d.has("signin-user-windows"):
        d.type("signin-user-windows", windows[0])
        d.type("signin-windows-password", windows[1])


def fits(d: Driver) -> None:
    assert d.overflow() == [], d.overflow()


def said(text: str) -> str:
    """What a label shows: its rich text without the markup."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).replace("\xa0", " ").split())


def words(d: Driver, name: str) -> str:
    """Everything a row or a card says, as one string."""
    return " ".join(said(part.text()) for part in d.find(name).findChildren(QLabel))


# ---------------------------------------------------------------------------------------------
# The frame
# ---------------------------------------------------------------------------------------------
def test_theme_generation():
    dark = build_stylesheet("dark")
    assert "#1b1f24" in dark and "#23282f" in dark and "#199e70" in dark
    assert "chevron_down_dark.svg" in dark
    light = build_stylesheet("light")
    assert "#f6f7f9" in light and "#ffffff" in light and "chevron_down_light.svg" in light


def test_window_opens_on_connect_with_no_made_up_defaults(session):
    d, window, _world = session.start(connect=False)
    assert window.windowTitle() == "Telegraf Helper, for VCF Operations"
    assert (window.width(), window.height()) == (1000, 700)
    assert d.title() == "Connect to VCF Operations"
    assert not window.side.isVisible()
    for name in ("connect-address", "connect-user", "connect-password"):
        assert d.text(name) == ""
    fits(d)


def test_first_launch_names_what_is_missing_and_clears_as_you_type(session):
    d, window, _world = session.start(connect=False)
    d.click("connect-go")
    assert d.page() == "connect"
    notes = [f.note.text() for f in window.connect_fields.values()]
    assert notes == ["! Enter the address of VCF Operations.", "! Enter your user name.", "! Enter your password."]
    d.type("connect-address", "o")
    assert window.connect_fields["address"].input.property("class") != "bad"
    assert "same address you type in a browser" in window.connect_fields["address"].note.text()


def test_connect_lands_on_machines_ready_to_use_and_accepts_a_pasted_address(session):
    d, window, _world = session.start(connect=False)
    d.type("connect-address", "  https://ops.example.com/ui/login?x=1 ")
    d.type("connect-user", "admin")
    d.type("connect-password", " pw with spaces ")
    d.click("connect-go")
    assert d.page() == "machines"
    assert window.conn.url == "https://ops.example.com"
    assert window.conn.password == " pw with spaces ", "a password is used exactly as typed"
    banner = d.find("banner").text.text()
    assert "Connected to ops.example.com. The app is ready." in banner and "It found 8 machines" in banner
    assert window.side.isVisible() and d.has("nav-machines") and d.has("nav-activity") and d.has("nav-settings")
    assert d.text("nav-badge") == "1 to fix"
    fits(d)


def test_cannot_reach_refused_and_tried_again_are_three_different_messages(session):
    d, window, world = session.start(connect=False)
    d.type("connect-address", "ops.example.com")
    d.type("connect-user", "admin")
    d.type("connect-password", "pw")
    world.connect = "unreachable"
    d.click("connect-go")
    first = d.find("connect-error").text.text()
    assert "Could not reach ops.example.com." in first and "still the same" not in first
    d.click("connect-go")
    assert "Tried again at" in d.find("connect-error").text.text()
    world.connect = "ok"
    d.type("connect-password", "wrong")
    d.click("connect-go")
    refused = d.find("connect-error").text.text()
    assert "did not accept that name or password" in refused and "still the same" not in refused
    assert not window.connected
    d.type("connect-password", "pw")
    d.click("connect-go")
    assert d.page() == "machines"


def test_unknown_certificate_is_shown_trusted_once_and_remembered(session):
    d, window, world = session.start(connect=False)
    world.connect = "cert"
    d.type("connect-address", "ops.example.com")
    d.type("connect-user", "admin")
    d.type("connect-password", "pw")
    d.click("connect-go")
    assert d.page() == "certificate" and d.title() == "One check before connecting"
    details = d.text("certificate-details")
    assert "Synthetic Corp Internal CA" in details and "March 4, 2028" in details and "ops.example.com" in details
    d.click("certificate-cancel")
    assert d.page() == "connect" and "you cancelled at the certificate check" in d.find("connect-note").text.text()
    assert window.state_store.trusted_certificate("ops.example.com") is None
    d.type("connect-password", "pw")
    d.click("connect-go")
    d.click("certificate-trust")
    assert d.page() == "machines"
    assert window.conn.pinned_cert_sha256 == standins.CERT_SHA and window.conn.verify_ssl
    window.close()
    # the next launch asks only for the password, and does not ask about the certificate again
    d, window, _world = session.start(connect=False, world=world, seed=False)
    assert d.title() == "Welcome back"
    assert QApplication.focusWidget().objectName() == "connect-password"
    assert d.text("connect-address") == "ops.example.com" and d.text("connect-user") == "admin"
    assert d.text("connect-password") == ""
    assert "is trusted on this computer" in d.text("connect-cert-trusted")
    d.type("connect-password", "pw")
    d.click("connect-go")
    assert d.page() == "machines"


def test_a_changed_certificate_is_refused_and_shown_again(session):
    d, window, world = session.start(connect=False)
    world.connect = "cert"
    window.state_store.trust_certificate("ops.example.com", {"sha256": "aa" * 32})
    d.type("connect-address", "ops.example.com")
    d.type("connect-user", "admin")
    d.type("connect-password", "pw")
    d.click("connect-go")
    assert d.page() == "certificate"
    assert "has changed since you trusted it" in d.text("certificate-details")
    assert d.find("certificate-trust").text() == "Trust the new certificate and connect"


def test_access_token_instead_of_a_password(session):
    d, window, _world = session.start(connect=False)
    d.click("connect-use-token")
    assert d.has("connect-token") and not d.has("connect-user")
    d.type("connect-address", "ops.example.com")
    d.click("connect-go")
    assert window.connect_fields["token"].note.text() == "! Paste your access token."
    d.type("connect-token", "a-token")
    d.click("connect-go")
    assert d.page() == "machines" and window.conn.use_token
    assert "an access token" in d.find("side-connection").accessibleName()
    assert window.state_store.list_environments()[0].token is None


def test_company_login_uses_the_source_vcf_operations_offers(session):
    d, window, _world = session.start(connect=False)
    d.type("connect-address", "ops.example.com")
    d.type("connect-user", "jsmith")
    d.type("connect-password", "pw")
    d.choose("connect-source", "My company login")
    d.click("connect-go")
    assert d.page() == "machines" and window.conn.auth_source == "corp.example.com"


def test_no_machines_is_explained_not_shown_as_an_empty_table(session):
    world = standins.World()
    world.connect = "none"
    d, _window, _world = session.start(world=world)
    assert d.text("machines-none") == "VCF Operations did not list any machines"
    assert d.has("machines-retry") and d.has("machines-sign-out")


def test_inventory_failure_is_an_error_with_a_way_forward(session):
    d, window, _world = session.start()
    window.adapter.list_virtual_machines = lambda strict=False: (_ for _ in ()).throw(RuntimeError("HTTP 500"))
    d.click("machines-refresh")
    assert "could not be read from VCF Operations" in d.find("machines-error").text.text()
    assert d.has("machines-retry")


# ---------------------------------------------------------------------------------------------
# Machines
# ---------------------------------------------------------------------------------------------
def test_machines_list_statuses_filters_search_and_ticks(session):
    d, window, _world = session.start()
    assert d.find("status-web-prod-01").status == "Sending data"
    assert d.find("status-app-win-01").status == "VCF Operations' own agent"
    assert d.find("status-k8s-node-01").status == "Needs attention"
    assert d.find("action-db-pg-01").text() == "Set up" and d.find("action-k8s-node-01").text() == "See problem"
    assert not d.has("status-legacy-app-01") and not d.has("open-vCLS-4f2a9c1e")
    assert d.find("chip-all").text().split()[-1] == "8" and d.find("chip-none").text().split()[-1] == "5"
    d.click("chip-attention")
    assert d.has("status-k8s-node-01") and not d.has("status-db-pg-01")
    d.click("chip-all")
    d.type("machines-find", "10.20.2")
    assert d.has("status-db-pg-01") and d.has("status-db-sql-02") and not d.has("status-web-prod-01")
    assert d.find("chip-none").text().split()[-1] == "2", "counts follow the search"
    d.type("machines-find", "nothing-like-this")
    assert d.text("machines-empty") == "No machine matches"
    d.click("machines-show-all")
    assert d.has("selection-hint")
    d.click("tick-db-pg-01")
    d.click("tick-files-01")
    assert "2 machines ticked" in d.text("selection-count")
    assert d.find("selection-setup").text().startswith("Set up monitoring on 2 machines")
    d.click("selection-clear")
    assert window.selection == [] and d.has("selection-hint")
    d.click("show-off")
    assert d.has("status-legacy-app-01") and not d.has("tick-legacy-app-01")
    d.click("show-infra")
    assert not d.has("action-vCLS-4f2a9c1e") and not d.has("tick-vCLS-4f2a9c1e")
    fits(d)


def test_tick_all_keeps_machines_ticked_under_another_filter(session):
    d, window, _world = session.start()
    d.click("tick-web-prod-01")
    first = list(window.selection)
    assert len(first) == 1
    d.click("chip-none")
    assert not d.has("tick-web-prod-01")
    assert "1 machine ticked" in d.text("selection-count")
    assert "1 not shown" in d.text("selection-count")
    d.click("tick-all")
    assert first[0] in window.selection and len(window.selection) > 1
    d.click("tick-all")
    assert window.selection == first
    assert "1 machine ticked" in d.text("selection-count")


def test_machine_page_shows_status_actions_and_history(session):
    d, _window, _world = session.start()
    d.click("open-web-prod-01")
    assert d.title() == "web-prod-01"
    assert "Sending data to VCF Operations" in d.find("machine-headline").title.text()
    assert d.has("machine-change") and d.has("machine-remove") and d.has("history-seed-nov3")
    d.click("machine-back")
    d.click("action-k8s-node-01")
    headline = d.find("machine-headline")
    assert "did not finish" in headline.title.text()
    assert "What happened:" in headline.small.text() and "What to do:" in headline.small.text()
    assert d.find("machine-retry").text() == "Try again"
    fits(d)


# ---------------------------------------------------------------------------------------------
# Set up one machine
# ---------------------------------------------------------------------------------------------
def test_set_up_one_machine_start_to_finish(session):
    d, window, world = session.start()
    d.click("action-db-pg-01")
    assert d.page() == "signin" and d.text("flow-title") == "SET UP MONITORING"
    d.click("flow-next")
    assert d.page() == "signin", "empty fields keep the person on the step and say what is missing"
    sign_in(d)
    d.click("flow-next")
    assert d.title() == "The machine is ready"
    assert "Ready. Telegraf 1.40.1 will be installed." in words(d, "check-db-pg-01")
    d.click("flow-next")
    assert d.page() == "collect"
    assert "also found PostgreSQL running" in d.text("page-sub")
    assert "opt-sug" in d.find("opt-pg").property("class"), "found but not watched is dashed"
    fits(d)
    d.click("flow-next")
    assert "Found but not watched:" in d.find("review-unwatched").text.text()
    assert d.find("flow-next").text() == "Start setup on db-pg-01"
    d.click("flow-next")
    assert d.page() == "result"
    headline = d.find("result-headline")
    assert headline.title.text() == "db-pg-01 is set up" and headline.kind == "ok"
    assert "PostgreSQL was found here but is not watched" in words(d, "result-db-pg-01")
    command = d.text("command-text")
    assert command.startswith("vcf-telegraf-helper run --replace-inputs \\\n") and "..." not in command
    assert '--vcf-pass "<password>"' in command and '--ssh-pass "<password>"' in command and "pw" not in command.split()
    assert "--vm-name db-pg-01" in command and "--install-telegraf --telegraf-version 1.40.1" in command
    d.click("command-copy")
    assert QApplication.clipboard().text() == command
    fits(d)
    d.click("flow-next")
    assert d.page() == "machines" and d.find("status-db-pg-01").status == "Sending data"
    assert world.machine("db-pg-01").telegraf_installed
    entry = window.history.entries()[0]
    assert entry.title == "Set up monitoring" and entry.rows[0].cmd == command and entry.worked == 1
    assert "pw" not in window.history.history_file.read_text().split('"')


def test_found_database_asks_for_an_account_and_never_shows_its_password(session):
    d, window, _world = session.start()
    d.click("action-db-pg-01")
    sign_in(d)
    d.click("flow-next")
    d.click("flow-next")

    def fill() -> None:
        d.click("database-test", settle=False)
        d.pump(60)
        assert "Enter the database user name and password." in d.text("database-error")
        d.type("database-user", "monitor")
        d.type("database-password", "db-secret-value")
        d.click("database-test", settle=False)
        d.wait(lambda: d.has("database-found"), what="databases found")
        assert "Found 3 databases" in d.find("database-found").text.text()
        d.click("database-add", settle=False)

    d.in_dialog(fill, lambda: d.click("tick-pg", settle=False))
    assert window.flow.choices.has("pg") and "3 databases found." in window.flow.choices.notes["pg"]
    d.click("flow-next")
    assert not d.has("review-unwatched")

    def look() -> None:
        text = d.text("text-dialog-text")
        assert "[[inputs.postgresql]]" in text and "db-secret-value" not in text
        d.click("text-dialog-close", settle=False)

    d.in_dialog(look, lambda: d.click("review-files", settle=False))
    d.click("flow-next")
    command = d.text("command-text")
    assert "--postgres" in command and "password=<password>" in command and "db-secret-value" not in command
    assert "db-secret-value" not in window.history.history_file.read_text()


def test_practice_run_changes_nothing_and_says_so(session):
    d, window, world = session.start()
    d.click("action-db-pg-01")
    sign_in(d)
    d.click("flow-next")
    d.click("flow-next")
    d.click("flow-next")
    d.click("flow-practice")
    assert d.page() == "result"
    assert d.find("result-headline").title.text() == "Practice run finished. Nothing was changed."
    assert "--dry-run" in d.text("command-text")
    assert not world.machine("db-pg-01").telegraf_installed
    assert d.find("flow-next").text() == "Start the real setup"
    d.click("flow-done")
    assert d.find("status-db-pg-01").status == "Not set up"
    assert window.history.entries()[0].title == "Practice run: Set up monitoring"


def test_test_connection_only_is_recorded_and_changes_nothing(session):
    d, window, world = session.start()
    d.click("tick-db-pg-01")
    d.click("tick-k8s-node-01")
    d.click("selection-test")
    sign_in(d)
    assert d.find("flow-next").text().startswith("Test connection")
    d.click("flow-next")
    assert d.title() == "1 machine passed. 1 did not."
    assert "This test is saved" in d.text("check-saved")
    assert not d.has("check-fix-k8s-node-01"), "a test names the problem; the fix belongs to a setup"
    entry = window.history.entries()[0]
    assert entry.kind == "test" and entry.worked == 1 and entry.failed == 1 and not any(r.cmd for r in entry.rows)
    assert not world.machine("db-pg-01").telegraf_installed
    d.click("flow-next")
    assert d.page() == "machines"


# ---------------------------------------------------------------------------------------------
# Several machines
# ---------------------------------------------------------------------------------------------
def start_several(d: Driver) -> None:
    for name in ("db-pg-01", "web-prod-02", "build-runner-03", "k8s-node-01", "app-win-01"):
        d.click(f"tick-{name}")
    d.click("selection-setup")
    sign_in(d)
    d.click("flow-next")


def test_several_machines_problems_first_each_with_its_fix(session):
    d, window, _world = session.start()
    start_several(d)
    assert "app-win-01 is left out of this run" not in d.title()
    assert d.title() == "2 machines are ready. 2 have a problem."
    assert d.text("flow-what") == "5 machines picked, 4 in this run"
    from vcf_ops_telegraf_helper.gui.widgets import Row

    rows = [w.objectName() for w in d.find("check-list").findChildren(Row)]
    assert rows[:2] == ["check-build-runner-03", "check-k8s-node-01"], "problems are listed first"
    assert d.find("check-fix-build-runner-03").text() == "Use a different sign-in"
    assert d.find("check-fix-k8s-node-01").text() == "Pick a collector it can reach"
    go = d.find("flow-next")
    assert go.text().startswith("Continue with 2, leave 2 out") and "primary" not in go.property("class")
    fits(d)

    def own_signin() -> None:
        d.type("signin-dialog-user", "runner")
        d.type("signin-dialog-password", "pw")
        d.click("signin-dialog-ok", settle=False)

    d.in_dialog(own_signin, lambda: d.click("check-fix-build-runner-03", settle=False))
    assert "build-runner-03 uses its own (runner)" in d.text("check-signed")
    d.click("check-again-k8s-node-01")
    assert "still the same" in words(d, "check-k8s-node-01")
    d.click("check-leave-k8s-node-01")
    assert d.title() == "All 3 machines are ready"
    assert d.find("flow-next").text().startswith("Continue with 3, leave 1 out")
    d.click("flow-next")
    d.click("flow-next")
    left = d.find("review-left-out").text.text()
    assert "Left out of this run (2)" in left and "app-win-01" in left and "k8s-node-01" in left
    assert d.find("flow-next").text() == "Start setup on 3 machines"
    fits(d)
    d.click("flow-next")
    headline = d.find("result-headline")
    assert headline.kind == "warn"
    assert headline.title.text() == "3 of the 5 machines you picked are set up. 2 were left out."
    assert d.has("result-setup-k8s-node-01") and not d.has("result-setup-app-win-01")
    assert d.find("command-picker").count() == 3
    d.click("command-copy-all")
    assert QApplication.clipboard().text().count("vcf-telegraf-helper run") == 3
    entry = window.history.entries()[0]
    assert entry.total == 5 and entry.worked == 3 and entry.not_done == 2
    fits(d)


def test_a_refused_sign_in_is_tried_again_after_the_password_is_corrected(session):
    d, _window, _world = session.start()
    d.click("tick-db-pg-01")
    d.click("selection-setup")
    sign_in(d, linux=("svc-monitor", "wrong"))
    d.click("flow-next")
    assert d.find("check-fix-db-pg-01").text() == "Use a different sign-in"

    def corrected() -> None:
        d.type("signin-dialog-user", "svc-monitor")
        d.type("signin-dialog-password", "right")
        d.click("signin-dialog-ok", settle=False)

    d.in_dialog(corrected, lambda: d.click("check-fix-db-pg-01", settle=False))
    d.wait(lambda: d.title() == "The machine is ready", what="the machine to pass its check")
    assert "Not tried here" not in words(d, "check-db-pg-01")


def test_the_app_never_says_all_for_fewer_than_were_picked(session):
    d, _window, _world = session.start()
    d.click("tick-db-pg-01")
    d.click("tick-web-prod-02")
    d.click("selection-setup")
    sign_in(d)
    d.click("flow-next")
    d.click("flow-next")
    d.click("flow-next")
    d.click("flow-next")
    assert d.find("result-headline").title.text() == "Both machines are set up"
    assert d.find("result-headline").kind == "ok"


def test_stop_leaves_machines_not_yet_changed_alone_and_says_which(session, monkeypatch):
    monkeypatch.setattr(standins, "DELAY", 0.02)
    d, window, world = session.start()
    for name in ("db-pg-01", "db-sql-02", "files-01", "web-prod-02"):
        d.click(f"tick-{name}")
    d.click("selection-setup")
    sign_in(d)
    d.click("flow-next")
    d.click("flow-next")
    d.click("flow-next")
    d.click("flow-next", settle=False)
    d.wait(lambda: d.page() == "running" and d.has("run-stop"), what="stop button")
    stop = d.find("run-stop")
    assert stop.text() == "Stop the machines not yet changed"
    d.click("run-stop", settle=False)
    assert window.findChild(QPushButton, "run-stop") is stop, "the button is one persistent widget, never rebuilt"
    assert not stop.isVisible(), "once pressed there is nothing more to stop"
    d.settle()
    entry = window.history.entries()[0]
    stopped = [r.name for r in entry.rows if r.skipped]
    assert stopped, "at least the machines waiting their turn are stopped"
    for name in stopped:
        assert not world.machine(name).telegraf_installed or name == "web-prod-02"
        assert not world.machine(name).uploaded_files, f"{name} was stopped, so nothing was written to it"
    title = d.find("result-headline").title.text()
    done = len(entry.rows) - len(stopped)
    assert d.find("result-headline").kind in ("warn", "info")
    assert title.startswith(f"{done} of the 4 machines you picked") or title == "Stopped. Nothing was changed."
    assert all(not r.cmd for r in entry.rows if r.skipped)


def test_a_refused_sign_in_is_not_tried_on_more_machines(session):
    d, window, world = session.start()
    for machine in world.executors.values():
        machine.accepts = {"someone-else"}
    for name in ("db-pg-01", "web-prod-02", "build-runner-03", "k8s-node-01"):
        d.click(f"tick-{name}")
    d.click("selection-setup")
    sign_in(d)
    d.click("flow-next")
    held = [r for r in window.flow.check.values() if "could lock the account" in r.what]
    assert len(held) >= 2, "after the first refusals the same sign-in is not tried again"
    assert d.title() == "0 machines are ready. 4 have a problem."
    assert d.text("check-blocked").startswith("No machine is ready yet")


# ---------------------------------------------------------------------------------------------
# Failure, retry, sending Telegraf across
# ---------------------------------------------------------------------------------------------
def test_try_again_keeps_the_choices_and_offers_a_collector_the_machine_can_reach(session):
    d, window, world = session.start()
    d.click("action-k8s-node-01")
    d.click("machine-retry")
    assert d.text("flow-title") == "TRY AGAIN"
    sign_in(d)
    d.click("flow-next")
    assert d.title() == "k8s-node-01 is not ready yet"

    def pick() -> None:
        assert "cannot reach it" in d.text("collector-reach-0") and "can reach it" in d.text("collector-reach-2")
        assert not d.has("collector-use-0")
        d.click("collector-use-2", settle=False)

    d.in_dialog(pick, lambda: d.click("check-fix-k8s-node-01", settle=False))
    assert d.title() == "The machine is ready"
    d.click("flow-next")
    assert d.page() == "review", "the choices from the last attempt are kept: no What to collect step"
    assert d.text("review-same") == "The same choices as the last attempt."
    d.click("flow-next")
    assert d.find("result-headline").title.text() == "k8s-node-01 is set up"
    assert "--collector 10.30.0.51" in d.text("command-text")
    d.click("flow-next")
    assert d.page() == "machine" and "Sending data" in d.find("machine-headline").title.text()
    d.click("nav-activity")
    d.click("activity-open-seed-yesterday")
    assert d.find("result-headline").small.text() == "It has been put right since: see below."
    assert not d.has("result-retry-k8s-node-01")


def test_machine_without_internet_gets_telegraf_sent_from_this_computer(session):
    d, window, world = session.start()
    world.machine("files-01").internet = False
    d.click("action-files-01")
    sign_in(d)
    d.click("flow-next")
    assert d.find("check-fix-files-01").text() == "Send it from this computer"
    d.click("check-fix-files-01")
    assert d.title() == "The machine is ready"
    d.click("flow-next")
    d.click("flow-next")
    assert "Send Telegraf 1.40.1 from this computer" in words(d, "review-files-01")
    d.click("flow-next")
    command = d.text("command-text")
    assert "--install-telegraf --telegraf-version 1.40.1 --send-telegraf" in command and "--winrm-ssl" in command
    machine = world.machine("files-01")
    assert machine.telegraf_installed
    assert any("Get-FileHash" in c for c in machine.executed_commands), "the machine checks the copy it received"
    assert not any("Invoke-WebRequest" in c for c in machine.executed_commands)
    headline = d.find("result-headline")
    assert headline.kind == "info" and "Waiting for its first data" in headline.small.text()
    d.click("flow-next")
    assert d.find("status-files-01").status == "Set up, waiting for first data"
    d.click("open-files-01")
    d.click("machine-check-now")
    assert "Sending data" in d.find("machine-headline").title.text()


def test_a_failure_during_the_run_names_the_reason_and_the_remedy(session):
    d, window, world = session.start()
    d.click("tick-db-pg-01")
    d.click("tick-files-01")
    d.click("selection-setup")
    sign_in(d)
    d.click("flow-next")
    d.click("flow-next")
    d.click("flow-next")
    world.machine("files-01").internet = False
    d.click("flow-next")
    headline = d.find("result-headline")
    assert headline.kind == "bad" and headline.title.text() == "1 of 2 machines set up. 1 needs attention."
    row = window.history.entries()[0].row_for("res-vm-008")
    assert not row.ok and "could not download Telegraf" in row.msg and "send it across" in row.todo
    assert d.has("result-retry-files-01")
    d.click("flow-next")
    assert d.find("status-files-01").status == "Needs attention"
    assert d.find("status-db-pg-01").status == "Sending data"
    assert d.text("nav-badge") == "2 to fix"


# ---------------------------------------------------------------------------------------------
# Change what is collected, and the app's own settings
# ---------------------------------------------------------------------------------------------
def test_change_applies_what_is_ticked_and_confirms_it(session):
    d, window, world = session.start()
    d.click("open-web-prod-01")
    d.click("machine-change")
    sign_in(d)
    d.click("flow-next")
    assert d.page() == "collect" and d.title() == "Change what web-prod-01 collects"
    assert d.find("tick-cpu").isChecked() and not d.find("tick-diskio").isChecked()
    d.click("flow-next")
    assert "You have not changed anything yet" in d.find("review-nothing").text.text()
    assert not d.has("flow-next"), "nothing to apply means no blue button"
    d.click("flow-back")
    d.click("tick-diskio")
    d.click("tick-swap")
    d.click("flow-next")
    assert d.has("review-add") and d.has("review-rem")
    assert d.find("flow-next").text() == "Apply the change to web-prod-01"
    d.click("flow-next")
    assert d.find("result-headline").title.text() == "The change is in effect on web-prod-01"
    row = window.history.entries()[0].rows[0]
    assert "read the settings back from the machine" in row.msg
    assert "Newly collecting: Disk activity" in row.msg and "No longer collecting: Swap use" in row.msg
    assert "--replace-inputs" in d.text("command-text") and "--no-swap" in d.text("command-text")
    deployed = world.machine("web-prod-01").uploaded_files["/etc/telegraf/telegraf.d/vcf-helper-system.conf"]
    assert "[[inputs.diskio]]" in deployed and "[[inputs.swap]]" not in deployed
    d.click("flow-next")
    assert d.page() == "machine"
    assert window.history.facts("res-vm-001")["collecting"] == ["cpu", "mem", "disk", "net", "system", "diskio"]


def test_settings_are_saved_and_shown_at_work_in_the_next_setup(session):
    d, window, _world = session.start()
    d.click("nav-settings")
    d.click("settings-tab-defaults")
    d.choose("setting-existing", "Update it")
    assert d.find("saved-existing").isVisible()
    assert "Check step of the next setup" in d.text("saved-says-existing")
    d.choose("setting-version", "1.34.0")
    d.choose("setting-collector", "main-cp-01")
    d.click("default-diskio")
    assert 'will also collect "Disk activity"' in d.text("saved-says-defaults")
    fits(d)
    window.close()
    # the settings survive closing the app
    d, window, _world = session.start(seed=False)
    assert window.settings.existing == "update" and window.settings.version == "1.34.0"
    d.click("action-web-prod-02")
    sign_in(d)
    d.click("flow-next")
    row = words(d, "check-web-prod-02")
    assert "It will be updated to version 1.34.0" in row and "(your choice in Settings, New setups)" in row
    assert 'the collector "main-cp-01" (your choice in Settings, New setups)' in row
    d.click("flow-next")
    assert "(as you set it in Settings, New setups)" in d.text("page-sub")
    assert d.find("tick-diskio").isChecked()
    d.click("flow-next")
    assert "This is the collector you chose in Settings, New setups." in " ".join(
        part.text() for part in d.window.page.findChildren(QLabel))
    d.click("flow-cancel")
    assert d.page() == "machines"


def test_colours_change_straight_away_and_are_remembered(session):
    d, window, _world = session.start()
    assert window.current_theme == "dark"
    d.click("nav-settings")
    d.click("settings-tab-look")
    d.click("setting-theme-light")
    assert window.current_theme == "light" and d.find("saved-theme").isVisible()
    window.close()
    _d, window, _world = session.start(connect=False, seed=False)
    assert window.current_theme == "light"


def test_settings_connection_test_says_three_things_in_words(session):
    d, _window, _world = session.start()
    d.click("side-connection")
    assert d.page() == "settings"
    d.click("settings-test")
    assert "Everything works between this computer and VCF Operations." in d.find("settings-test-ok").text.text()
    result = words(d, "settings-test-result")
    assert re.search(r"Read the list of machines \(\d+\) and collectors \(\d+\)", result), result
    assert result.count("collectors") == 1, result
    fits(d)


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_no_text_is_set_smaller_than_eleven_and_a_half_pixels(theme):
    from vcf_ops_telegraf_helper.gui.theme import build_stylesheet

    sizes = [float(size) for size in re.findall(r"font-size:\s*([\d.]+)px", build_stylesheet(theme))]
    assert sizes and min(sizes) >= 11.5, sorted(set(sizes))


# ---------------------------------------------------------------------------------------------
# Takeover
# ---------------------------------------------------------------------------------------------
def test_take_over_needs_the_name_typed_and_keeps_the_same_record(session):
    d, window, world = session.start()
    d.click("action-app-win-01")
    assert d.text("flow-title") == "TAKE OVER A MACHINE"
    assert not d.has("signin-key-toggle"), "VCF Operations needs a password to remove its own agent"
    sign_in(d)
    d.click("flow-next")
    assert d.title() == "The machine is ready"
    d.click("flow-next")
    assert d.page() == "takeover-review" and d.title() == "Take over app-win-01"
    fits(d)
    d.click("flow-next")
    assert d.page() == "takeover-review"
    assert said(window.flow.typed_field.note.text()) == "! Type the machine's name to confirm."
    d.type("takeover-typed", "app-win-1")
    d.click("flow-next")
    assert window.flow.typed_field.note.text() == "! That does not match. Type app-win-01 exactly."
    assert world.machine("app-win-01").managed, "nothing happens without the right name"
    d.click("flow-practice")
    assert "Practice run passed. Nothing was changed." in d.find("takeover-practice").text.text()
    assert world.machine("app-win-01").managed and window.journal.list_records() == []
    d.type("takeover-typed", "app-win-01")
    d.click("flow-next", settle=False)
    assert not d.has("run-stop"), "a takeover cannot be stopped part-way, so no Stop is offered"
    d.settle(90)
    assert d.page() == "result"
    assert d.find("result-headline").title.text() == "app-win-01 has been taken over"
    command = d.text("command-text")
    assert "--take-over-managed-agent --confirm-takeover app-win-01" in command and "--vm-id vm-1005" in command
    assert "--install-telegraf" not in command and "--replace-inputs" not in command
    assert not world.machine("app-win-01").managed and world.machine("app-win-01").telegraf_installed
    record = window.journal.list_records()[0]
    assert record.state == "verified" and "Typed 'app-win-01' to confirm" in record.confirmation_text
    d.click("flow-next")
    assert d.find("status-app-win-01").status == "Sending data"


def interrupted_world(home: Path):
    from test_takeover import FIXTURE

    from vcf_ops_telegraf_helper.models.vcf import AgentObjectInfo
    from vcf_ops_telegraf_helper.storage.journal import TakeoverJournal, TakeoverRecord

    world = standins.World()
    world.retired = True
    world.machine("app-win-01").retire()
    journal = TakeoverJournal(home / "state" / "takeovers")
    record = TakeoverRecord(
        vm_name="app-win-01", vm_mor="vm-1005", vc_id=standins.vc_id(5), vm_resource_id="res-vm-005",
        target_hostname="10.20.3.41", os_family="windows", ops_url="https://ops.example.com",
        collector_address=standins.GROUP.address, state="retired", confirmation_text="Typed 'app-win-01'",
        managed_services=["ucp-telegraf"], uninstall_task_id="task-standin-1",
        agent_object_before=AgentObjectInfo(resource_id="agent-vm-1005", managed_type="Product Managed", stat_key_count=137))
    journal.write_backup(record, {"telegraf.conf": FIXTURE.read_text(), "agent-object-before.json": "{}"})
    journal.save(record)
    return world


def test_interrupted_takeover_is_surfaced_and_finished_without_repeating_anything(session, tmp_path):
    world = interrupted_world(tmp_path / "home")
    d, window, world = session.start(world=world)
    assert d.find("status-app-win-01").status == "Takeover interrupted"
    assert "stopped half-way" in d.find("banner-interrupted").text.text()
    assert d.find("action-app-win-01").text() == "Finish takeover"
    d.click("banner-finish-takeover")
    assert d.text("flow-title") == "FINISH A TAKEOVER"
    sign_in(d)
    d.click("flow-next")
    assert d.page() == "takeover-resume" and "stopped after step 2" in d.text("page-sub")
    fits(d)
    d.click("flow-next", settle=False)
    d.settle(90)
    assert d.find("result-headline").title.text() == "app-win-01 has been taken over"
    assert window.history.entries()[0].title == "Finish a takeover"
    assert world.task_polls == 0, "the removal was not asked for a second time"
    d.click("flow-next")
    assert d.find("status-app-win-01").status == "Sending data"


def test_closing_is_refused_while_a_machine_is_being_changed(session, monkeypatch):
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QMessageBox

    d, window, _world = session.start()
    d.click("action-db-pg-01")
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    from vcf_ops_telegraf_helper.gui.flow import RunState

    window.flow.run = RunState(["res-vm-003"], 5, False)
    window.flow.run.changing.add("res-vm-003")
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted() and "working on a machine right now" in warned[0]
    # the whole of a real run is protected, not only the moment a machine is being written to
    window.flow.run.changing.clear()
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted(), "closing mid-run would lose the record of machines already changed"
    window.flow.run.practice = True
    assert not window.flow.changing_now(), "a practice run changes nothing, so it does not hold the app open"
    window.flow.run = None
    window.working_on = "res-vm-001"
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted(), "nor can the app be closed while monitoring is being removed"
    window.working_on = None


# ---------------------------------------------------------------------------------------------
# Activity, removal
# ---------------------------------------------------------------------------------------------
def test_activity_finds_a_past_run_with_its_command_and_saves_a_report(session, tmp_path, monkeypatch):
    d, window, _world = session.start()
    d.click("nav-activity")
    assert d.title() == "Activity"
    d.type("activity-find", "web-prod")
    assert d.has("activity-open-seed-nov3") and not d.has("activity-open-seed-yesterday")
    d.type("activity-find", "zzz")
    assert d.text("activity-no-match") == 'Nothing matches "zzz"'
    d.type("activity-find", "")
    d.click("activity-open-seed-nov3")
    assert d.page() == "entry" and d.title() == "Set up monitoring"
    assert d.find("result-headline").title.text() == "This worked"
    assert d.text("command-text").startswith("vcf-telegraf-helper run")
    fits(d)
    report = tmp_path / "report.md"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(report), "")))
    d.click("entry-report")
    text = report.read_text()
    assert "web-prod-01" in text and "Do this again from the command line" in text and "<password>" in text
    d.click("entry-back")
    assert d.page() == "activity"


def test_activity_empty_state(session):
    d, _window, _world = session.start(seed=False)
    d.click("nav-activity")
    assert d.text("activity-empty") == "Nothing has been done yet"
    d.click("activity-go-machines")
    assert d.page() == "machines"


def test_remove_monitoring_asks_first_and_is_recorded_with_its_command(session):
    d, window, world = session.start()
    d.click("open-web-prod-01")

    def keep() -> None:
        d.click("remove-cancel", settle=False)

    d.in_dialog(keep, lambda: d.click("machine-remove", settle=False))
    assert d.page() == "machine" and world.machine("web-prod-01").telegraf_installed
    from PySide6.QtCore import QTimer

    def give_signin() -> None:
        d.type("signin-dialog-user", "svc-monitor")
        d.type("signin-dialog-password", "pw")
        d.click("signin-dialog-ok", settle=False)

    def confirm() -> None:
        d.click("remove-ok", settle=False)
        QTimer.singleShot(150, give_signin)

    d.in_dialog(confirm, lambda: d.click("machine-remove", settle=False))
    d.settle()
    assert d.page() == "machines"
    assert "Monitoring was removed from web-prod-01." in d.find("banner").text.text()
    assert d.find("status-web-prod-01").status == "Not set up"
    assert not world.machine("web-prod-01").telegraf_installed
    entry = window.history.entries()[0]
    assert entry.kind == "remove" and entry.rows[0].cmd.startswith("vcf-telegraf-helper uninstall --target 10.20.1.11")
    assert '--password "<password>"' in entry.rows[0].cmd


# ---------------------------------------------------------------------------------------------
# Things that must stay true of every screen
# ---------------------------------------------------------------------------------------------
def test_an_ampersand_in_a_machine_name_is_shown_not_swallowed(session):
    from vcf_ops_telegraf_helper.gui.widgets import button

    assert button("Take over R&D-01").text() == "Take over R&&D-01"
    d, _window, _world = session.start()
    for widget in d.window.findChildren(QPushButton):
        assert "&amp;" not in widget.text()


def test_about_names_the_app_and_shows_the_licences(session):
    d, _window, _world = session.start()

    def look() -> None:
        assert d.text("dialog-title") == "Telegraf Helper"

        def licences() -> None:
            assert "PySide6" in d.text("text-dialog-text")
            d.click("text-dialog-close", settle=False)

        from PySide6.QtCore import QTimer

        QTimer.singleShot(100, licences)
        d.click("about-licences", settle=False)
        d.click("about-close", settle=False)

    d.in_dialog(look, lambda: d.click("side-about", settle=False))


def test_update_notice_is_shown_in_the_left_column_and_can_be_switched_off(session):
    from vcf_ops_telegraf_helper.updates import ReleaseNotice

    d, window, _world = session.start()
    window._show_release_notice(ReleaseNotice("v9.9.9", "https://example.invalid/v9.9.9"))
    d.pump(60)
    assert "New version available: v9.9.9" in d.text("update-notice")
    window.settings.updates = False
    window.render_side()
    d.pump(60)
    assert not d.has("update-notice")


def test_macos_gui_does_not_require_linux_display_variables(qapp, monkeypatch, tmp_path):
    import sys

    from vcf_ops_telegraf_helper.gui import app as gui_app

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("VCF_HELPER_GUI_SMOKE_REPORT", str(tmp_path / "window.png"))
    monkeypatch.setattr(QApplication, "exec", lambda self=None: 0)
    assert gui_app.run_gui() == 0


def test_fresh_window_does_not_touch_the_real_home(session, tmp_path):
    _d, window, _world = session.start(connect=False)
    assert tmp_path in window.state_store.state_file.parents
    assert tmp_path in window.history.history_file.parents and tmp_path in window.journal.directory.parents
    assert isinstance(window, MainWindow) and isinstance(window.state_store, StateStore)


# ---------------------------------------------------------------------------------------------
# Headlines count against what was picked, in every combination
# ---------------------------------------------------------------------------------------------
def _entry(rows, kind="setup", practice=False):
    from vcf_ops_telegraf_helper.storage.history import ActivityEntry, ActivityRow

    made = [ActivityRow(machine_id=f"m{i}", name=f"machine-{i}", **row) for i, row in enumerate(rows)]
    return ActivityEntry(title="Set up monitoring", kind=kind, practice=practice, rows=made)


OK, FAILED, LEFT, STOPPED, WAITING = {}, {"ok": False, "msg": "x"}, {"left": True}, {"skipped": True}, {"pending": True}


@pytest.mark.parametrize("rows,practice,kind,expected,tone", [
    ([OK, OK], False, "setup", "Both machines are set up", "ok"),
    ([OK, OK, OK], False, "setup", "All 3 machines are set up", "ok"),
    ([OK, WAITING], False, "setup", "Both machines are set up", "ok"),
    ([WAITING], False, "setup", "machine-0 is set up", "info"),
    ([OK, LEFT], False, "setup", "1 of the 2 machines you picked is set up. 1 was left out.", "warn"),
    ([OK, OK, LEFT, STOPPED], False, "setup", "2 of the 4 machines you picked are set up. 2 were left out or stopped.", "warn"),
    ([OK, FAILED], False, "setup", "1 of 2 machines set up. 1 needs attention.", "bad"),
    ([OK, OK, OK, FAILED, LEFT], False, "setup",
     "3 of the 5 machines you picked are set up. 1 needs attention, and 1 was left out.", "bad"),
    ([STOPPED, STOPPED], False, "setup", "Stopped. Nothing was changed.", "info"),
    ([OK], True, "setup", "Practice run finished. Nothing was changed.", "info"),
    ([OK, STOPPED, LEFT], True, "setup",
     "Practice run finished for 1 of the 3 machines you picked. Nothing was changed.", "warn"),
    ([FAILED], True, "setup", "The practice run found a problem. Nothing was changed.", "bad"),
    ([FAILED], False, "change", "The change was not applied to machine-0", "bad"),
    ([FAILED], False, "takeover", "The takeover of machine-0 did not finish", "bad"),
])
def test_live_headline_never_overstates(qapp, rows, practice, kind, expected, tone):
    from vcf_ops_telegraf_helper.gui.results import live_headline

    headline = live_headline(_entry(rows, kind, practice))
    assert said(headline.title.text()) == expected and headline.kind == tone


def test_saved_headline_matches_what_the_run_really_did(session):
    from vcf_ops_telegraf_helper.gui.results import saved_headline

    _d, window, _world = session.start()
    assert saved_headline(window, _entry([OK])).kind == "ok"
    waiting = saved_headline(window, _entry([OK, WAITING]))
    assert waiting.kind == "info" and "1 of 2 were still waiting for first data" in waiting.title.text()
    assert saved_headline(window, _entry([{"note": True}])).kind == "ok", "a note on a setup is not a warning"
    assert saved_headline(window, _entry([{"note": True}], kind="takeover")).kind == "warn"
    failed = saved_headline(window, _entry([FAILED]))
    assert failed.kind == "bad" and "put right since" not in failed.small.text(), "nothing later worked, so it does not say so"
    partly = saved_headline(window, _entry([OK, LEFT]))
    assert partly.kind == "warn" and "1 of the 2 machines picked was set up" in partly.title.text()


def test_a_failed_line_offers_its_fix_and_claims_nothing_it_cannot_show(session):
    d, window, _world = session.start()
    # a change that failed on a machine that is still sending data: the remedy is shown, with a way to try again
    from vcf_ops_telegraf_helper.storage.history import ActivityEntry, ActivityRow

    window.history.add(ActivityEntry(id="failed-change", title="Change what is collected", kind="change", rows=[ActivityRow(
        machine_id="res-vm-001", name="web-prod-01", ok=False, msg="Telegraf did not accept the new settings.",
        todo="The machine is as it was before.")]))
    d.click("nav-activity")
    d.click("activity-open-failed-change")
    line = words(d, "result-web-prod-01")
    assert "What to do: The machine is as it was before." in line and "later attempt" not in line
    assert d.find("result-retry-web-prod-01").text() == "Try again"
    d.click("result-retry-web-prod-01")
    assert d.text("flow-title") == "CHANGE WHAT IS COLLECTED"
