"""A guided path: the short run of steps for doing something to machines.

Set up monitoring, try again, test a connection, change what is collected, take a machine
over, and finish an interrupted takeover are all the same shape: sign in (skipped when the app
already holds the sign-in), a check that changes nothing, the choices, a review that is the
confirmation, and a result that says in a sentence what happened. One question per screen, the
blue button bottom right, the steps listed on the left.

The rule every screen here is held to: never report success for something that did not happen.
Count against what the person picked, name everything that was not done, and show a setting at
work in the place it applies.
"""

from __future__ import annotations

import threading
from datetime import datetime
from functools import partial
from queue import Queue
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QSizePolicy, QVBoxLayout, QWidget

from vcf_ops_telegraf_helper.gui import dialogs
from vcf_ops_telegraf_helper.gui.model import AUTO, Machine, SignIn, collector_key
from vcf_ops_telegraf_helper.gui.results import command_box, live_headline, result_list, save_report, technical_text
from vcf_ops_telegraf_helper.gui.widgets import (
    Banner,
    Card,
    Field,
    ListBox,
    OptionCard,
    Page,
    Progress,
    Row,
    Tags,
    box,
    busy_bar,
    button,
    esc,
    grid,
    hbox,
    label,
    link,
    mark,
    sentence,
    stretch,
    vbox,
)
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo
from vcf_ops_telegraf_helper.renderer.renderer import TelegrafRenderer
from vcf_ops_telegraf_helper.security.redaction import redact_secrets
from vcf_ops_telegraf_helper.storage.history import ActivityEntry, ActivityRow, format_clock, format_when
from vcf_ops_telegraf_helper.utils import local_now_formatted
from vcf_ops_telegraf_helper.workflow import checks as chk
from vcf_ops_telegraf_helper.workflow.batch import (
    BatchRunner,
    LeftOut,
    MachineOutcome,
    build_entry,
    run_pool,
    test_entry,
)
from vcf_ops_telegraf_helper.workflow.checks import CheckRequest, CheckResult, can_reach, check_machine, collector_label
from vcf_ops_telegraf_helper.workflow.collect import (
    CATALOG,
    DATABASE_ENGINES,
    DATABASE_KEYS,
    GROUP_BASICS,
    GROUP_SOFTWARE,
    GROUP_WINDOWS,
    WINDOWS_ONLY,
    CollectChoices,
    effective_keys,
    item as catalog_item,
    name_of,
    names_of,
    ordered,
)
from vcf_ops_telegraf_helper.workflow.plain import (
    COLLECTOR_IS,
    SAFE_STEPS,
    STATUS_ATTENTION,
    STATUS_MANAGED,
    STATUS_NONE,
    TAKEOVER_NOTES,
    join_names,
    plural,
    step_label,
    takeover_steps,
)
from vcf_ops_telegraf_helper.workflow.plan import (
    DEFAULT_TELEGRAF_VERSION,
    INSTALL,
    KEEP,
    PUSH,
    UPDATE,
    MachinePlan,
    placeholder_monitoring,
)

SETUP, TEST, TAKEOVER, CHANGE = "setup", "test", "takeover", "change"
SIGNIN, CHECK, COLLECT, REVIEW, RUN = "signin", "check", "collect", "review", "run"
FOUND_NOTE = "Switched on because it was found."
SETTING_NOTE = " (your choice in Settings, New setups)"
DOTS = "\u2026"
ARROW = "\u2192"
BACK = "\u2190 Back"

TITLES = {SETUP: "Set up monitoring", TEST: "Test connection", TAKEOVER: "Take over a machine",
          CHANGE: "Change what is collected"}


class RunState:
    """A run in progress, then its result."""

    def __init__(self, ids: List[str], steps: int, practice: bool):
        self.ids = ids
        self.total = steps
        self.practice = practice
        self.step: Dict[str, int] = {i: -1 for i in ids}
        self.outcomes: Dict[str, MachineOutcome] = {}
        self.changing: set = set()
        self.runner: Optional[BatchRunner] = None
        self.stopping = False
        self.finished = False
        self.entry: Optional[ActivityEntry] = None
        self.plans: List[MachinePlan] = []


class Flow:
    def __init__(self, win, kind: str, ids: List[str], retry: bool = False, resume: bool = False, origin=None):
        self.win = win
        self.kind = kind
        self.ids = list(ids)
        self.retry = retry
        self.resume = resume
        self.origin = origin or ("machines", None, None)
        self.step = SIGNIN
        self.dropped: List[str] = []
        self.out: List[str] = []
        self.creds: Dict[str, SignIn] = {
            os: (win.session.family[os].copy() if os in win.session.family else SignIn()) for os in ("linux", "windows")
        }
        self.per: Dict[str, SignIn] = {i: win.session.machine[i] for i in ids if i in win.session.machine}
        self.check: Dict[str, Optional[CheckResult]] = {}
        self.previous_problem: Dict[str, str] = {}
        self.coll: Dict[str, CollectorInfo] = {}
        self.coll_all: Optional[CollectorInfo] = None
        self.push: Dict[str, bool] = {}
        self.update: Dict[str, bool] = {}
        self.update_from_setting = False
        self.choices = CollectChoices(keys=list(win.settings.defaults))
        self.before: List[str] = []
        self.primed = False
        self.edit_collect = False
        self.typed = ""
        self.auto = False
        self.busy = False
        self.signin_error: Optional[str] = None
        self.practice_state: Optional[dict] = None
        self.run: Optional[RunState] = None
        self.test_entry_id: Optional[str] = None
        self.record = None  # the journal record of an interrupted takeover
        self._generation = 0
        self._tickets: Dict[str, int] = {}
        self._lock = threading.Lock()
        self._refused: set = set()
        self._accepted: set = set()
        self._alive = True

    # ------------------------------------------------------------------ helpers
    def machine(self, machine_id: str) -> Machine:
        return self.win.machine(machine_id)

    @property
    def machines(self) -> List[Machine]:
        return [self.machine(i) for i in self.ids]

    @property
    def one(self) -> bool:
        return len(self.ids) == 1

    @property
    def is_test(self) -> bool:
        return self.kind == TEST

    @property
    def version(self) -> str:
        return self.win.settings.version

    def families(self) -> Dict[str, List[Machine]]:
        return {os: [m for m in self.machines if m.os == os and m.id not in self.per] for os in ("linux", "windows")}

    def signin_for(self, machine: Machine) -> SignIn:
        return self.per.get(machine.id) or self.creds[machine.os]

    def steps(self) -> List[tuple]:
        if self.kind == TEST:
            return [(SIGNIN, "Sign in"), (CHECK, "Test results")]
        if self.kind == TAKEOVER:
            if self.resume:
                return [(SIGNIN, "Sign in"), (REVIEW, "Where it stopped"), (RUN, "Results")]
            return [(SIGNIN, "Sign in"), (CHECK, "Check"), (REVIEW, "Review and confirm"), (RUN, "Results")]
        if self.kind == CHANGE:
            return [(SIGNIN, "Sign in"), (COLLECT, "What to collect"), (REVIEW, "Review and apply"), (RUN, "Results")]
        steps = [(SIGNIN, "Sign in"), (CHECK, "Check"), (COLLECT, "What to collect"), (REVIEW, "Review and start"),
                 (RUN, "Results")]
        if self.retry and not self.edit_collect:
            steps = [s for s in steps if s[0] != COLLECT]
        return steps

    def title(self) -> str:
        if self.resume:
            return "Finish a takeover"
        if self.retry:
            return "Try again"
        return TITLES[self.kind]

    def picked(self) -> int:
        return len(self.ids) + len(self.dropped)

    def what(self) -> str:
        if self.picked() == 1:
            return esc(self.machine((self.ids + self.dropped)[0]).name)
        text = f"{plural(self.picked(), 'machine')} picked"
        if self.kind == TEST:
            return text
        showing = self.dropped or ((self.run or self.step in (REVIEW, COLLECT)) and self.left_out())
        if showing:
            count = (len(self.ids) - len(self.out)) if self.step in (SIGNIN, CHECK) else len(self.ready())
            text += f", {count} in this run"
        return text

    def ready(self) -> List[str]:
        return [i for i in self.ids if self.check.get(i) is not None and self.check[i].ok and i not in self.out]

    def left_out(self) -> List[LeftOut]:
        """Every machine the person picked that this run will not touch, with the reason."""
        managed = 'It is monitored by VCF Operations\' own agent; use "Take over" on its row.'
        found = [LeftOut(machine_id=i, name=self.machine(i).name, os=self.machine(i).os, why=managed) for i in self.dropped]
        ready = self.ready()
        for machine_id in self.ids:
            if machine_id in ready:
                continue
            result = self.check.get(machine_id)
            if machine_id in self.out:
                why = result.what if (result is not None and result.state == chk.BAD) else "You left it out."
            elif result is None:
                why = "It was not checked."
            elif result.state == chk.SKIP:
                why = managed
            else:
                why = result.what
            machine = self.machine(machine_id)
            found.append(LeftOut(machine_id=machine_id, name=machine.name, os=machine.os, why=why))
        return found

    def effective_collector(self, machine_id: str) -> Optional[CollectorInfo]:
        result = self.check.get(machine_id)
        return self.coll.get(machine_id) or self.coll_all or (result.collector if result else None)

    def changing_now(self) -> bool:
        """True from the start of a real run until its result is recorded.

        A machine may already have been changed, or be about to be, at any moment of a real run,
        and the record of the run is written when it ends. Closing is refused for all of it.
        """
        return bool(self.run and not self.run.finished and not self.run.practice)

    def abandon(self) -> None:
        """The window is closing or the person left: stop what can be stopped and ignore late answers."""
        self._alive = False
        self._generation += 1
        if self.run and self.run.runner and not self.run.finished:
            self.run.runner.stop()

    # ------------------------------------------------------------------ the left column
    def fill_side(self, layout: QVBoxLayout) -> None:
        layout.addWidget(label(self.title().upper(), "flow-h", name="flow-title"))
        layout.addSpacing(4)
        layout.addWidget(label(self.what(), "flow-what", name="flow-what"))
        layout.addSpacing(10)
        steps = self.steps()
        shown = REVIEW if (self.run and self.run.practice and self.step == RUN) else self.step
        index = next((n for n, (key, _text) in enumerate(steps) if key == shown), 0)
        if self.practice_state is None and self.run and self.run.finished and not self.run.practice:
            index = len(steps) - 1
        for number, (_key, text) in enumerate(steps):
            state = "done" if number < index else ("now" if number == index else "")
            frame = QFrame()
            frame.setProperty("class", "step-now" if state == "now" else "step")
            frame.setObjectName(f"step-{number + 1}")
            num = label(mark("ok") if state == "done" else str(number + 1), f"num-{state}" if state else "num", wrap=False)
            num.setFixedSize(20, 20)
            num.setAlignment(Qt.AlignCenter)
            frame.setLayout(hbox(num, (label(text, f"step-text-{state}" if state else "step-text"), 1),
                                 spacing=9, margins=(8, 7, 8, 7)))
            layout.addWidget(frame)
        layout.addStretch(1)
        running = bool(self.run and not self.run.finished)
        finished = bool(self.run and self.run.finished) or (
            self.is_test and self.step == CHECK and not self.working())
        if not running and not finished and not self.busy:
            cancel = button("Cancel and go back", self.cancel, name="flow-cancel")
            cancel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            layout.addWidget(cancel)
            layout.addSpacing(4)
            layout.addWidget(label("The takeover stays unfinished if you cancel." if self.resume
                                   else "Nothing is changed if you cancel now.", "small", name="flow-cancel-note"))

    def show(self, page: Page) -> Page:
        return self.win.set_page(page, "flow")

    # ------------------------------------------------------------------ start
    def start(self) -> None:
        win = self.win
        if self.kind == SETUP and len(self.ids) > 1:
            self.dropped = [i for i in self.ids if self.machine(i).status == STATUS_MANAGED]
            self.ids = [i for i in self.ids if i not in self.dropped]
            if not self.ids:
                win.leave_flow()
                win.show_machines(("warn", "<b>Nothing to set up.</b> Every machine you ticked is monitored by VCF "
                                   "Operations' own agent. Replacing that is a takeover, done one machine at a time: use "
                                   '"Take over" on each row.'))
                return
        if self.retry:
            self._load_last_choices()
        if self.resume:
            vm = self.machines[0].vm
            try:
                self.record = win.journal.load(vm.vc_id, vm.vm_mor)
            except Exception:
                self.record = None
        needed = [os for os, machines in self.families().items() if machines]
        have = all(win.session.family.get(os) and win.session.family[os].complete for os in needed)
        if self.kind == TAKEOVER:
            # VCF Operations needs a password to remove its own agent; a key file cannot be handed to it
            have = have and all(not self.signin_for(m).use_key for m in self.machines)
        if have:
            self.auto = True
            self.after_signin()
        else:
            self.show_signin()

    def _load_last_choices(self) -> None:
        entry = self.win.history.last_change(self.ids[0])
        saved = (entry.choices or {}).get("collect") if entry else None
        if not saved:
            self.edit_collect = True
            return
        try:
            self.choices = CollectChoices.model_validate(saved)
        except Exception:
            self.edit_collect = True
            return
        for key in self.choices.missing_secrets():
            # The database password is never kept on disk, so that item has to be asked again
            self.choices.remove(key)
            self.edit_collect = True
        if self.choices.had_custom:
            # Nor are someone's own Telegraf settings: they may hold a password
            self.edit_collect = True
            self.lost_custom = True
        if not self.choices.keys:
            self.choices = CollectChoices(keys=list(self.win.settings.defaults))
            self.edit_collect = True

    # ------------------------------------------------------------------ step 1: sign in
    def show_signin(self) -> None:
        self.step = SIGNIN
        families = self.families()
        one = self.one
        both = bool(families["linux"] and families["windows"])
        page = Page("signin")
        target = esc(self.machines[0].name) if one else "the machines"
        if self.is_test:
            why = (f"To test the connection, the app signs in to {'the machine' if one else 'each machine'} and looks "
                   "around. It changes nothing.")
        elif self.resume:
            why = "To finish the takeover, the app signs in to the machine again."
        elif self.kind == TAKEOVER:
            why = ("To take the machine over, the app signs in to it. VCF Operations uses the same sign-in to remove its "
                   "own agent.")
        elif self.kind == CHANGE:
            why = "To change what the machine collects, the app signs in to it and reads what it collects today."
        else:
            why = ("To install and set up Telegraf (the small program that collects the measurements), the app signs in to "
                   f"{'the machine' if one else 'each machine'}.")
        if not one:
            why += (" One sign-in is used for all machines of the same kind. If one machine needs a different one, you can "
                    "give it on the next screen.")
        page.title(f"Sign in to {target}", why)
        if self.dropped:
            names = join_names([esc(self.machine(i).name) for i in self.dropped])
            single = len(self.dropped) == 1
            page.add(Banner(
                "warn", f"<b>{names} {'is' if single else 'are'} left out of this run.</b> "
                f"{'It is' if single else 'They are'} monitored by VCF Operations' own agent. Replacing that is a takeover, "
                f"done one machine at a time: use \"Take over\" on {'its' if single else 'each'} row afterwards.",
                name="signin-dropped"))
        if self.signin_error:
            page.add(Banner("bad", self.signin_error, name="signin-error"))
        self.signin_fields: Dict[str, tuple] = {}
        if self.busy:
            card = Card()
            card.add(label(f"Signing in{DOTS}", "small", name="signin-busy"), busy_bar())
            page.add(card).end()
            self.show(page)
            return
        hints = {
            "linux": ('Use an account that is allowed to install software on the machine (the "root" account, or one with '
                      'full "sudo" rights). Not sure? Just try it: the next screen tells you if the account is not allowed.'),
            "windows": ("Use an account that is an administrator on the machine. Not sure? Just try it: the next screen "
                        "tells you if it is not."),
        }
        for os, title in (("linux", "Linux"), ("windows", "Windows")):
            machines = families[os]
            if not machines:
                continue
            signin = self.creds[os]
            card = Card()
            names = label(f'<b style="font-size:14px">{title}</b>&nbsp; {esc(", ".join(m.name for m in machines))}', "small")
            head = hbox((names, 1))
            if both:
                count = len(machines)
                head.addWidget(link(f"Leave {'this machine' if count == 1 else f'these {count}'} out",
                                    lambda o=os: self._drop_family(o), name=f"signin-drop-{os}"))
            card.body.addLayout(head)
            those = "this machine" if len(machines) == 1 else "these machines"
            user = Field(f"User name on {those}", name=f"signin-user-{os}", value=signin.user,
                         placeholder="for example: svc-monitor" if os == "linux"
                         else "for example: administrator, or CORP\\svc-monitor")
            user.input.textEdited.connect(lambda text, s=signin: setattr(s, "user", text))
            allow_key = os == "linux" and self.kind != TAKEOVER
            secret = dialogs.KeyOrPassword(self.win, signin, allow_key, f"signin-{os}")
            card.body.addLayout(hbox((user, 1), (secret, 1), spacing=12, align=Qt.AlignTop))
            foot = hbox((label(hints[os], "small"), 1), align=Qt.AlignTop)
            if allow_key:
                toggle = link("", name="signin-key-toggle")

                def flip(s=signin, t=toggle, k=secret) -> None:
                    s.use_key = not s.use_key
                    t.setText("Use a password instead" if s.use_key else "Use a key file instead of a password")
                    k.refresh()

                toggle.setText("Use a password instead" if signin.use_key else "Use a key file instead of a password")
                toggle.clicked.connect(lambda _c=False, f=flip: f())
                foot.addWidget(toggle)
            card.body.addLayout(foot)
            page.add(card)
            self.signin_fields[os] = (user, secret)
        page.add(label("The app works out how to reach each machine by itself. It remembers this sign-in until you close "
                       "the app, and never saves it to disk.", "small"))
        page.end()
        if self.is_test:
            next_text = "Test connection"
        elif self.kind == CHANGE or self.resume:
            next_text = "Sign in"
        else:
            next_text = f"Check {'the machine' if one else 'the machines'}"
        page.footer(stretch, button(f"{next_text} {ARROW}", self.signin_next, "primary big", name="flow-next"))

        def focus() -> None:
            for os in ("linux", "windows"):
                if os in self.signin_fields:
                    user, secret = self.signin_fields[os]
                    (user.input if not user.text() else secret.password.input).setFocus()
                    return

        page.on_enter = focus
        self.show(page)

    def _drop_family(self, os: str) -> None:
        self.ids = [i for i in self.ids if self.machine(i).os != os or i in self.per]
        self.show_signin()

    def signin_next(self) -> None:
        self.auto = False
        self.signin_error = None
        missing = False
        for os, (user, secret) in self.signin_fields.items():
            signin = self.creds[os]
            if not signin.user.strip():
                user.set_error("Enter a user name.")
                missing = True
            if signin.use_key and self.kind != TAKEOVER and os == "linux":
                if not signin.key_file:
                    secret.set_error("Choose the key file.")
                    missing = True
            elif not signin.password:
                secret.set_error("Enter the password.")
                missing = True
        if missing:
            return
        for os in self.signin_fields:
            self.win.session.family[os] = self.creds[os].copy()
        self.after_signin()

    def after_signin(self) -> None:
        self._refused.clear()
        self._accepted.clear()
        if self.kind == CHANGE or self.resume:
            # No separate check screen: sign in, read, move on
            self.busy = True
            machine = self.machines[0]
            if self.auto:
                page = Page("reading")
                page.title(f"Reading what {esc(machine.name)} collects today{DOTS}" if self.kind == CHANGE
                           else f"Signing in to {esc(machine.name)}{DOTS}", "Signed in with the sign-in you gave earlier.")
                page.add(busy_bar(420)).end()
                self.step = COLLECT if self.kind == CHANGE else REVIEW
                self.show(page)
            else:
                self.show_signin()
            self.run_checks(list(self.ids), then=self._after_read)
            return
        self.step = CHECK
        self.out = []
        self.run_checks(list(self.ids))
        self.show_check()

    def _after_read(self) -> None:
        self.busy = False
        machine = self.machines[0]
        result = self.check.get(machine.id)
        if result is None or not result.ok:
            # The sign-in or the read did not work: say why on the Sign in step, where it can be put right
            what = result.what if result else "The app could not sign in to the machine."
            todo = result.todo if result else "Check the sign-in and try again."
            self.signin_error = f"<b>{esc(what)}</b><br>{esc(todo)}"
            self.win.session.family.pop(machine.os, None)
            self.win.session.machine.pop(machine.id, None)
            self.per.pop(machine.id, None)
            self.creds[machine.os].password = ""
            self.auto = False
            self.show_signin()
            return
        if self.kind == CHANGE:
            self.choices = result.current.model_copy(deep=True) if result.current else CollectChoices()
            self.before = effective_keys(self.choices.keys, machine.os)
            self.win.history.remember(machine.id, collecting=list(self.choices.keys))
            self.show_collect()
        else:
            self.show_review()

    # ------------------------------------------------------------------ step 2: check
    def working(self) -> bool:
        return any(self.check.get(i) is None for i in self.ids)

    def _request(self, machine: Machine) -> CheckRequest:
        win = self.win
        signin = self.signin_for(machine)
        chosen = self.coll.get(machine.id) or self.coll_all
        if chosen is None and win.settings.collector != AUTO:
            chosen = next((c for c in win.collectors if collector_key(c) == win.settings.collector), None)
        facts = win.history.facts(machine.id)
        kind = chk.KIND_RESUME if self.resume else self.kind
        return CheckRequest(
            machine=machine.ref(), kind=kind, username=signin.user.strip(), password=signin.password,
            key_file=signin.key_file if signin.use_key else None, many=not self.one, collectors=list(win.collectors),
            chosen=chosen, version=self.version,
            update=self.update.get(machine.id, win.settings.existing == "update"),
            send=bool(self.push.get(machine.id)), win_mode=win.settings.win_mode, ssh_port=win.settings.ssh_port,
            known_port=facts.get("port"), known_ssl=facts.get("winrm_ssl"),
        )

    def run_checks(self, ids: List[str], then=None) -> None:
        """Check these machines, two at a time. Each reports on its own as it finishes.

        Every check carries a ticket for its machine. Testing one machine again while others are
        still being checked replaces only that machine's ticket, so the others still report.
        """
        generation = self._generation
        win = self.win
        tickets = {}
        for machine_id in ids:
            previous = self.check.get(machine_id)
            if previous is not None and previous.state == chk.BAD:
                self.previous_problem[machine_id] = previous.what
            self.check[machine_id] = None
            self._tickets[machine_id] = self._tickets.get(machine_id, 0) + 1
            tickets[machine_id] = self._tickets[machine_id]
        requests = {i: self._request(self.machine(i)) for i in ids}

        def one(machine_id: str) -> None:
            request = requests[machine_id]
            account = self._account(request)
            with self._lock:
                held = account in self._refused and account not in self._accepted
            if held:
                # A sign-in already refused elsewhere is not tried again here: that could lock the account
                result = CheckResult(
                    machine_id=machine_id, state=chk.BAD, fix=chk.FIX_SIGNIN, refused=True,
                    what="Not tried here: another machine did not accept this sign-in, and trying it again could lock "
                         "the account.",
                    todo="Give this machine a sign-in that works on it, or go back and correct the sign-in for all of them.")
            else:
                try:
                    result = check_machine(request, win.make_executor)
                except Exception as exc:
                    win.logger.exception("Check of %s failed", request.machine.name)
                    result = CheckResult(
                        machine_id=machine_id, state=chk.BAD, fix=chk.FIX_AGAIN,
                        what="The app could not check this machine.",
                        todo=f"Press Test again. What the app saw: {redact_secrets(str(exc))[:200]}")
                with self._lock:
                    if result.refused:
                        self._refused.add(account)
                    elif result.signed_in:
                        self._accepted.add(account)
            win.tasks.post(lambda: self._check_answer(generation, tickets[machine_id], machine_id, result))

        win.tasks.run(lambda: run_pool([lambda i=i: one(i) for i in ids]),
                      done=lambda _r: self._checks_done(generation, then),
                      failed=lambda _e: self._checks_done(generation, then), name="checks")

    @staticmethod
    def _account(request) -> tuple:
        return (request.machine.os, request.username, request.key_file or "")

    def _check_answer(self, generation: int, ticket: int, machine_id: str, result: CheckResult) -> None:
        if not self._alive or generation != self._generation or machine_id not in self.ids \
                or self._tickets.get(machine_id) != ticket:
            return  # the person moved on, or asked for this machine to be tested again
        before = self.previous_problem.pop(machine_id, None)
        if result.state == chk.BAD and before and before == result.what:
            result.same_as_before = True
        machine = self.machine(machine_id)
        if result.signed_in:
            facts = {"port": result.port, "winrm_ssl": result.winrm_ssl}
            if result.installed_version:
                facts["telegraf"] = result.installed_version
            self.win.history.remember(machine_id, **facts)
        if result.ok and result.installed_version and machine_id not in self.update:
            self.update[machine_id] = self.win.settings.existing == "update" and result.can_update
            self.update_from_setting = self.update_from_setting or self.update[machine_id]
        self.check[machine_id] = result
        if machine is not None and self.step == CHECK and self.win.flow is self:
            if self.is_test and not self.working():
                self._record_test()
            self.show_check()

    def _checks_done(self, generation: int, then) -> None:
        if not self._alive or generation != self._generation:
            return
        if then is not None:
            then()

    def _record_test(self) -> None:
        rows = []
        for machine_id in self.ids:
            machine, result = self.machine(machine_id), self.check[machine_id]
            bad = result.state == chk.BAD
            rows.append(ActivityRow(
                machine_id=machine_id, name=machine.name, os=machine.os, ok=not bad,
                msg=result.what if bad else self.ok_message(machine, result), todo=result.todo if bad else ""))
        entry = test_entry(rows, self.test_entry_id)
        self.test_entry_id = entry.id
        self.win.history.add(entry)

    def ok_message(self, machine: Machine, result: CheckResult) -> str:
        test = self.is_test
        version = self.version
        installed = result.installed_version
        if self.push.get(machine.id):
            return "Ready. Telegraf will be sent from this computer, because the machine cannot download it."
        if self.kind == TAKEOVER:
            text = "Ready to take over. VCF Operations' agent is running, and its settings can be read and saved."
        elif result.managed:
            text = "Signed in. VCF Operations' own agent is running here and sending data."
        elif installed and result.monitored:
            text = (f"Signed in. Telegraf {installed} is set up here and can reach the collector." if test else
                    f"Ready. This machine is already monitored (Telegraf {installed}). If you continue, what it collects "
                    "is replaced with the choices you make next.")
        elif installed:
            text = (f"Signed in, and the machine can reach the collector. Someone has already installed Telegraf "
                    f"{installed} here, but it is not reporting to VCF Operations yet." if test else
                    f"Ready. Someone has already installed Telegraf {installed} here; it is not reporting to VCF "
                    "Operations yet.")
        elif test:
            text = ("Signed in, and the machine can reach the collector. Telegraf (the small program that collects the "
                    "measurements) is not installed yet.")
        elif machine.status == STATUS_ATTENTION:
            text = f"Ready. The problem from the last attempt is out of the way. Telegraf {version} will be installed."
        else:
            text = f"Ready. Telegraf {version} will be installed{SETTING_NOTE if version != DEFAULT_TELEGRAF_VERSION else ''}."
        if machine.id in self.coll:
            text += f" Its data will go to {collector_label(self.coll[machine.id])}."
        elif self.win.settings.collector != AUTO and result.collector is not None and not test \
                and collector_key(result.collector) == self.win.settings.collector:
            text += f" Its data will go to {collector_label(result.collector)}{SETTING_NOTE}."
        return text

    def show_check(self) -> None:
        self.step = CHECK
        one, test = self.one, self.is_test
        working = self.working()
        ready = self.ready()
        bad = [i for i in self.ids if self.check.get(i) is not None and self.check[i].state == chk.BAD and i not in self.out]
        each = "it" if one else "each one"
        if working:
            title = f"Testing{DOTS}" if test else f"Checking the machine{'' if one else 's'}{DOTS}"
            sub = (f"Signing in{'' if one else ' to each machine'}, seeing what is already installed, and making sure "
                   f"{each} can reach the collector. {COLLECTOR_IS}")
        elif test:
            if bad:
                title = "The test found a problem" if one else f"{plural(len(ready), 'machine')} passed. {len(bad)} did not."
            else:
                title = "The connection works" if one else "All connections work"
            sub = (f"The app signed in to {'the machine' if one else 'each machine'} and checked that it can reach the "
                   f"collector. {COLLECTOR_IS} Nothing was changed.")
        else:
            if bad:
                title = (f"{esc(self.machines[0].name)} is not ready yet" if one else
                         f"{plural(len(ready), 'machine is', 'machines are')} ready. "
                         f"{len(bad)} {'has' if len(bad) == 1 else 'have'} a problem.")
            elif not ready:
                title = "No machine is in this run"
            else:
                title = "The machine is ready" if len(ready) == 1 else f"All {len(ready)} machines are ready"
            sub = (("Fix the problem below to carry on. " if one else "Fix the problems listed first, or leave those "
                    "machines out. ") if bad else "") + f"This check changes nothing on {'the machine' if one else 'any machine'}."
        page = Page("check")
        page.title(title, sub)
        listing = ListBox(max_height=440, name="check-list")
        order = list(self.ids)
        if not working:
            order.sort(key=lambda i: 0 if (self.check[i].state == chk.BAD and i not in self.out) else 1)
        for machine_id in order:
            listing.add_row(self._check_row(machine_id))
        page.add(listing)
        signed = self._signed_line()
        if signed is not None and not working:
            page.body.addLayout(signed)
        if test and not working:
            page.add(label("This test is saved. You can find it later under <b>Activity</b>.", "small", name="check-saved"))
        page.end()
        back = button(BACK, self.back, name="flow-back")
        if working:
            page.footer(label("This takes a few seconds per machine.", "note", name="check-working"))
        elif test:
            fresh = [i for i in ready if self.machine(i).status in (STATUS_NONE, STATUS_ATTENTION)]
            items = [back, stretch]
            if fresh:
                target = self.machine(fresh[0]).name if len(fresh) == 1 else plural(len(fresh), "machine")
                items.append(button(f"Set up monitoring on {target} {ARROW}", lambda: self._test_to_setup(fresh),
                                    name="check-to-setup"))
            items.append(button("Done", self.done, "primary big", name="flow-next"))
            page.footer(*items)
        elif ready:
            partial = not one and len(ready) < len(self.ids)
            text = f"Continue with {len(ready)}, leave {len(self.ids) - len(ready)} out" if partial else "Continue"
            # While problems remain only the fix buttons are blue; carrying on without them is plain
            page.footer(back, stretch, button(f"{text} {ARROW}", self.check_next, "big" if bad else "primary big",
                                              name="flow-next"))
        else:
            page.footer(back, stretch, label("Fix the problem above to continue." if one else
                                             "No machine is ready yet. Fix a problem above, or go back.", "note",
                                             name="check-blocked"))
        self.show(page)

    def _signed_line(self):
        if not (self.auto or self.per):
            return None
        users = [self.win.session.family[os].user for os, machines in self.families().items()
                 if machines and os in self.win.session.family]
        text = ""
        if users:
            text = (f"Signed in with the sign-in you gave {'earlier' if self.auto else 'on the last screen'} "
                    f"({esc(' and '.join(users))})")
        own = [f"{esc(self.machine(i).name)} uses its own ({esc(s.user)})" for i, s in self.per.items() if i in self.ids]
        if own:
            text += ("; " if text else "") + ", ".join(own)
        if not text:
            return None
        return sentence(text + ".", link("Use a different sign-in", self.change_signin, name="check-change-signin"),
                        name="check-signed")

    def change_signin(self) -> None:
        self.per.clear()
        for machine in self.machines:
            self.win.session.machine.pop(machine.id, None)
        self.auto = False
        self._generation += 1
        self.show_signin()

    def _check_row(self, machine_id: str) -> Row:
        machine, result = self.machine(machine_id), self.check.get(machine_id)
        test, one = self.is_test, self.one
        if result is None:
            row = Row(machine.name, machine.os_name, object_name=f"check-{machine.name}")
            row.say(f"Signing in and looking around{DOTS}", "small")
            row.add_middle(busy_bar())
            return row
        if machine_id in self.out:
            row = Row(machine.name, machine.os_name, object_name=f"check-{machine.name}")
            row.say(f"{mark('skip')} Left out of this run. Nothing will be done to it.")
            row.add_actions(button("Put back", lambda: self._put_back(machine_id), "sm", name=f"check-putback-{machine.name}"))
            return row
        if result.state == chk.SKIP:
            row = Row(machine.name, machine.os_name, object_name=f"check-{machine.name}")
            row.say(f"{mark('skip')} {esc(result.what)}<br><span style=\"font-size:12px\">{esc(result.todo)}</span>")
            return row
        if result.state == chk.BAD:
            row = Row(machine.name, machine.os_name, kind="bad", object_name=f"check-{machine.name}")
            text = f"{mark('bad')} <b>{esc(result.what)}</b>"
            if result.same_as_before:
                text += f"<br>Tested again at {esc(format_clock(result.tested_at))}: still the same."
            text += f'<br><span style="font-size:12px">{esc(result.todo)}</span>'
            row.say(text)
            again = button("Test again", lambda: self.recheck(machine_id), "sm", name=f"check-again-{machine.name}")
            fixes: list = []
            if result.fix == chk.FIX_SIGNIN:
                fixes.append(button("Use a different sign-in", lambda: self.fix_signin(machine_id), "primary sm",
                                    name=f"check-fix-{machine.name}"))
            elif result.fix == chk.FIX_COLLECTOR:
                fixes += [button("Pick a collector it can reach", lambda: self.fix_collector(machine_id), "primary sm",
                                 name=f"check-fix-{machine.name}"), again]
            elif result.fix == chk.FIX_SEND:
                fixes += [button("Send it from this computer", lambda: self.fix_send(machine_id), "primary sm",
                                 name=f"check-fix-{machine.name}"), again]
            else:
                fixes.append(again)
            if not one:
                fixes.append(button("Leave this machine out", lambda: self._leave_out(machine_id), "sm",
                                    name=f"check-leave-{machine.name}"))
            row.middle.addLayout(hbox(*fixes, stretch, spacing=6))
            return row
        row = Row(machine.name, machine.os_name, object_name=f"check-{machine.name}")
        row.say(f"{mark('ok')} {esc(self.ok_message(machine, result))}", "msg")
        show_version = bool(result.installed_version and not test and self.kind == SETUP and not self.push.get(machine_id))
        if show_version:
            from_setting = self.win.settings.existing == "update" and self.update.get(machine_id)
            if self.update.get(machine_id) and result.can_update:
                text = (f"It will be updated to version {self.version} and then set up"
                        f"{SETTING_NOTE if from_setting else ''}.")
                swap = f"Keep version {result.installed_version} instead"
            elif result.can_update:
                text = "The app will keep that version and set it up, which is the safe choice."
                swap = f"Update it to {self.version} instead"
            else:
                text = (f"The app keeps that version: it is already at or above {self.version}, and a version is never "
                        "moved down.")
                swap = ""
            links = [link(swap, lambda: self._toggle_update(machine_id), name=f"check-update-{machine.name}")] if swap else []
            row.middle.addLayout(sentence(text, *links, width=500))
        if not test and self.kind == SETUP and not one and result.monitored:
            row.add_actions(button("Leave this machine out", lambda: self._leave_out(machine_id), "sm",
                                   name=f"check-leave-{machine.name}"))
        return row

    def _leave_out(self, machine_id: str) -> None:
        if machine_id not in self.out:
            self.out.append(machine_id)
        self.show_check()

    def _put_back(self, machine_id: str) -> None:
        self.out = [i for i in self.out if i != machine_id]
        self.show_check()

    def _toggle_update(self, machine_id: str) -> None:
        self.update[machine_id] = not self.update.get(machine_id)
        self.show_check()

    def recheck(self, machine_id: str) -> None:
        self.run_checks([machine_id])
        self.show_check()

    def fix_signin(self, machine_id: str) -> None:
        machine = self.machine(machine_id)
        signin = dialogs.sign_in_dialog(self.win, machine, allow_key=self.kind != TAKEOVER, other_machines=not self.one)
        if signin is None:
            return
        self.per[machine_id] = signin
        self.win.session.machine[machine_id] = signin
        account = self._account(self._request(machine))
        with self._lock:
            self._refused.discard(account)
            self._accepted.discard(account)
        self.recheck(machine_id)

    def fix_send(self, machine_id: str) -> None:
        self.push[machine_id] = True
        self.recheck(machine_id)

    def fix_collector(self, machine_id: str) -> None:
        machine, result = self.machine(machine_id), self.check[machine_id]
        choices = [(r.collector, [] if r.ok else [machine.name]) for r in result.collectors]
        picked = dialogs.collector_dialog(self.win, [machine.name], choices)
        if picked is None:
            return
        self.coll[machine_id] = picked
        self.recheck(machine_id)

    def check_next(self) -> None:
        if self.kind == TAKEOVER:
            self.show_review()
        elif self.retry and not self.edit_collect:
            self.show_review()
        else:
            self.show_collect()

    def _test_to_setup(self, keep: List[str]) -> None:
        """Carry on from a passed test straight into setting those machines up."""
        self.kind = SETUP
        self.out = [i for i in self.ids if i not in keep]
        self.choices = CollectChoices(keys=list(self.win.settings.defaults))
        self.show_collect()

    # ------------------------------------------------------------------ step 3: what to collect
    def found_on(self, key: str) -> List[str]:
        return [self.machine(i).name for i in self.ready() if key in (self.check[i].found or [])]

    def found_list(self) -> List[str]:
        return [item.key for item in CATALOG if item.group == GROUP_SOFTWARE and self.found_on(item.key)]

    def unwatched(self) -> List[str]:
        return [k for k in self.found_list() if not self.choices.has(k)]

    def _prime(self) -> None:
        """First visit: switch on found software that needs nothing from the person."""
        if self.primed or self.kind == CHANGE or self.retry:
            return
        self.primed = True
        for key in self.found_list():
            if catalog_item(key).ask == "web" and not self.choices.has(key):
                self.choices.add(key)
                self.choices.notes[key] = FOUND_NOTE

    def show_collect(self) -> None:
        self.step = COLLECT
        win = self.win
        ids = self.ready()
        machines = [self.machine(i) for i in ids]
        one = len(ids) == 1
        any_windows = any(m.os == "windows" for m in machines)
        any_linux = any(m.os == "linux" for m in machines)
        self._prime()
        pending = self.unwatched()
        auto_on = [name_of(k) for k in self.found_list() if self.choices.notes.get(k) == FOUND_NOTE]
        from_settings = "" if win.settings.usual else " (as you set it in Settings, New setups)"
        if self.kind == CHANGE:
            title = f"Change what {esc(machines[0].name)} collects"
            sub = ("Ticked items are collected today. Tick or untick, then continue. You will see exactly what changes "
                   "before anything is done.")
        else:
            title = "What should be collected?"
            if self.retry:
                sub = "These are the choices from the last attempt. Change them if you like, then continue."
                if getattr(self, "lost_custom", False) and not self.choices.custom_toml.strip():
                    sub += (" Your own Telegraf settings from last time were not kept, because they can hold a password: "
                            "add them again with the link below if you still want them.")
            elif pending:
                it = "it" if len(pending) == 1 else "them"
                also = f", and so is {join_names(auto_on)}, which the app found running" if auto_on else ""
                asks = any(k in DATABASE_KEYS for k in pending)
                sub = (f"The usual set{from_settings} is ticked{also}. The app also found {join_names(names_of(pending))} "
                       f"running (dashed below). Tick {it} to watch {it} too"
                       f"{'; you will be asked for a database account' if asks else ''}. Or leave {it} for later.")
            elif auto_on:
                sub = (f"The usual set{from_settings} is ticked, and so is {join_names(auto_on)}, which the app found "
                       "running. Press Continue unless you want something else.")
            else:
                sub = f"The usual set{from_settings} is already ticked. Most people just press Continue."
        page = Page("collect", margins=(22, 12, 22, 8), spacing=5)
        page.title(title, sub)

        def card(entry) -> OptionCard:
            on = self.choices.has(entry.key)
            note = self.choices.notes.get(entry.key) if on else None
            where = self.found_on(entry.key) if entry.group == GROUP_SOFTWARE else []
            found = ""
            if where:
                found = "Found on this machine" if one else f"Found on {', '.join(where)}"
            change = None
            if on and note and entry.ask and entry.ask != "web":
                change = partial(self._ask, entry.key)
            widget = OptionCard(entry.key, entry.name, esc(note) if note else entry.hint, on, found, change)
            widget.toggled.connect(self._pick)
            return widget

        def group(key: str, title_text: str, note: str = "") -> None:
            head = hbox(label(title_text.upper(), "grp-h", wrap=False), spacing=8)
            if note:
                head.addWidget(label(note, "small"), 1)
            else:
                head.addStretch(1)
            page.body.addLayout(head)
            page.body.addWidget(grid(4, [card(e) for e in CATALOG if e.group == key], spacing=6))

        basics_note = ""
        if any_windows:
            basics_note = ("On Windows these are collected together, the Windows way: one tick collects them all there."
                           if not any_linux else "Windows machines collect these together, as one set.")
        group(GROUP_BASICS, "The basics", basics_note)
        group(GROUP_SOFTWARE, f"Software running on the machine{'' if one else 's'}",
              "Marked where the app found it running." if one else
              "Marked where the app found it. An item is switched on only on machines that run it.")
        if any_windows:
            group(GROUP_WINDOWS, "Windows machines only", "Linux machines skip these." if any_linux else "")
        custom = bool(self.choices.custom_toml.strip())
        tail = sentence(
            "Software that is not in this list is left alone."
            + (" Your own Telegraf settings will be added too." if custom else ""),
            link("Edit your own settings" if custom else "For experts: add your own Telegraf settings", self._ask_custom,
                 name="collect-custom"), width=560)
        defaults = win.settings.defaults
        chosen_plain = [k for k in self.choices.keys if self.choices.notes.get(k) != FOUND_NOTE]
        usual = ordered(chosen_plain) == ordered(defaults)
        if not usual and self.kind != CHANGE:
            tail.addWidget(link("Back to the usual set", self._reset_collect, name="collect-reset"))
        page.body.addLayout(tail)
        page.end()
        back = button(BACK, self.back, name="flow-back")
        if self.choices.keys:
            page.footer(back, stretch, button(f"Continue {ARROW}", self.show_review, "primary big", name="flow-next"))
        else:
            page.footer(back, stretch, label(
                "Tick at least one thing to collect." + (
                    " To stop monitoring altogether, cancel and use Remove monitoring on the machine's page."
                    if self.kind == CHANGE else ""), "note", name="collect-empty"))
        self.show(page)

    def _pick(self, key: str, on: bool) -> None:
        if self.choices.has(key):
            self.choices.remove(key)
            self.show_collect()
            return
        if catalog_item(key).ask:
            if self._ask(key):
                return
            self.show_collect()  # cancelled: put the tick back the way it was
            return
        self.choices.add(key)
        self.show_collect()

    def _reset_collect(self) -> None:
        self.choices = CollectChoices(keys=list(self.win.settings.defaults), custom_toml=self.choices.custom_toml)
        self.primed = False
        self.show_collect()

    def _ask_custom(self) -> None:
        text = dialogs.custom_dialog(self.win, self.choices.custom_toml)
        if text is None:
            return
        self.choices.custom_toml = text
        self.show_collect()

    def _first_where(self, key: str, windows_only: bool = False) -> Optional[str]:
        """A machine to ask: one where the software was found, otherwise the first that fits."""
        ready = self.ready()
        fits = [i for i in ready if not windows_only or self.machine(i).os == "windows"]
        return next((i for i in fits if key in (self.check[i].found or [])), fits[0] if fits else None)

    def _executor_for(self, machine_id: str):
        machine, result = self.machine(machine_id), self.check[machine_id]
        signin = self.signin_for(machine)
        plan = MachinePlan(
            machine_id=machine.id, name=machine.name, address=machine.address, os=machine.os,
            username=signin.user.strip(), password=signin.password, key_file=signin.key_file if signin.use_key else None,
            port=result.port, winrm_ssl=result.winrm_ssl)
        return self.win.make_executor(plan.target())

    def _ask(self, key: str) -> bool:
        """Open the small dialog behind a choice. True when the choice was switched on."""
        entry = catalog_item(key)
        win = self.win
        current = self.choices.details.get(key, {})
        if entry.ask == "db":
            where = self._first_where(key)

            def test(user: str, password: str, port: int) -> List[str]:
                if where is None:
                    return []
                executor = self._executor_for(where)
                try:
                    found = executor.discover_databases(db_type=DATABASE_ENGINES[key], auth_mode="password" if key != "mssql"
                                                        else "sql", username=user, password=password, port=port)
                    return [d.name for d in found]
                finally:
                    executor.close()

            answer = dialogs.database_dialog(win, key, bool(self.found_on(key)), test, current)
            if answer is None:
                return False
            count = len(answer.get("databases") or [])
            self.choices.details[key] = answer
            self.choices.notes[key] = f"{plural(count, 'database')} found." if count else "Account given; not tested."
        elif entry.ask == "ping":
            address = dialogs.ping_dialog(win, str(current.get("addr") or ""))
            if address is None:
                return False
            self.choices.details[key] = {"addr": address}
            self.choices.notes[key] = f"Pings {address}."
        elif entry.ask == "web":
            found = [self.machine(i).name for i in self.ready() if key in self.check[i].web_urls]
            missing = [self.machine(i).name for i in self.ready() if key in self.check[i].web_missing]
            if not dialogs.web_dialog(win, key, found, missing):
                return False
            self.choices.notes[key] = "Status page found." if found and not missing else (
                "Switched on. Its status page was not found everywhere." if (found or missing) else "Switched on.")
        elif entry.ask in ("svc", "perf"):
            targets = [i for i in self.ready() if self.machine(i).os == "windows"]
            services = entry.ask == "svc"
            objects: Dict[str, dict] = {}

            def fetch() -> List[tuple]:
                seen: Dict[str, str] = {}
                for machine_id in targets:
                    executor = self._executor_for(machine_id)
                    try:
                        if services:
                            for service in executor.discover_services():
                                if service.name and service.name.lower() != "telegraf":
                                    seen.setdefault(service.name, service.display_name or service.name)
                        else:
                            for found_set in executor.discover_perfmon_sets():
                                seen.setdefault(found_set.name, f"{found_set.name}: {found_set.description}"
                                                if found_set.description else found_set.name)
                                objects[found_set.name] = {
                                    "object_name": found_set.name, "counters": found_set.counters or ["*"],
                                    "measurement": "win_" + found_set.name.replace(" ", "_").replace(":", "_").lower()}
                    finally:
                        executor.close()
                return sorted(seen.items(), key=lambda pair: pair[1].lower())

            if services:
                picked = dialogs.pick_list_dialog(
                    win, entry.name, "These services were found on the Windows machines. Tick the ones to watch.", fetch,
                    selected=current.get("names", []), empty="No services could be read from the Windows machines.",
                    name="services-dialog")
                if not picked:
                    return False
                self.choices.details[key] = {"names": picked}
                self.choices.notes[key] = f"{plural(len(picked), 'service')} watched."
            else:
                picked = dialogs.pick_list_dialog(
                    win, entry.name, "These groups of counters were found on the Windows machines. Tick the ones to add.",
                    fetch, selected=[o.get("object_name") for o in current.get("objects", [])],
                    empty="No counters could be read from the Windows machines.", name="counters-dialog")
                if not picked:
                    return False
                self.choices.details[key] = {"objects": [objects[name] for name in picked if name in objects]}
                self.choices.notes[key] = f"{plural(len(picked), 'group')} of counters added."
        self.choices.add(key)
        self.show_collect()
        return True

    # ------------------------------------------------------------------ step 4: review
    def install_for(self, machine_id: str) -> str:
        result = self.check[machine_id]
        if self.kind == CHANGE:
            return KEEP
        if self.push.get(machine_id):
            return PUSH
        if self.kind == TAKEOVER or self.resume:
            return INSTALL
        if result.installed_version:
            return UPDATE if (self.update.get(machine_id) and result.can_update) else KEEP
        return INSTALL

    def plan_for(self, machine_id: str, practice: bool = False) -> MachinePlan:
        machine, result = self.machine(machine_id), self.check[machine_id]
        signin = self.signin_for(machine)
        collector = self.effective_collector(machine_id)
        if collector is None and self.record is not None and self.record.collector_address:
            collector = next((c for c in self.win.collectors if c.address == self.record.collector_address),
                             CollectorInfo(address=self.record.collector_address))
        if collector is None:
            collector = chk.preferred_collector(machine.ref(), self.win.collectors) or CollectorInfo(address="")
        single = len(self.ready()) == 1
        plan = MachinePlan(
            machine_id=machine.id, name=machine.name, address=machine.address, os=machine.os,
            vm_mor=machine.vm.vm_mor, vc_id=machine.vm.vc_id, kind=self.kind, username=signin.user.strip(),
            password=signin.password, key_file=signin.key_file if signin.use_key else None, port=result.port,
            winrm_ssl=result.winrm_ssl, collector=collector, collector_label=collector_label(collector),
            install=self.install_for(machine_id), version=self.version, installed_version=result.installed_version,
            choices=self.choices.model_copy(deep=True), found=list(result.found), web_urls=dict(result.web_urls),
            single=single or self.kind == CHANGE, practice=practice, resume=self.resume,
        )
        return plan

    def _what_happens(self, machine_id: str) -> str:
        result = self.check[machine_id]
        version = self.version
        install = self.install_for(machine_id)
        if install == PUSH:
            return f"Send Telegraf {version} from this computer, install it, then set it up."
        if result.monitored:
            more = f" Telegraf {result.installed_version} is updated to {version}." if install == UPDATE else ""
            return "Already monitored. What it collects is replaced with the choices below." + more
        if result.installed_version:
            if install == UPDATE:
                return f"Update Telegraf {result.installed_version} to {version}, then set it up."
            return f"Keep Telegraf {result.installed_version} and set it up."
        return f"Install Telegraf {version}, then set it up."

    def show_review(self) -> None:
        self.step = REVIEW
        if self.kind == TAKEOVER:
            if self.resume:
                self._show_resume()
            else:
                self._show_takeover_review()
            return
        if self.kind == CHANGE:
            self._show_change_review()
            return
        win = self.win
        ids = self.ready()
        one = len(ids) == 1
        page = Page("review", margins=(22, 14, 22, 12), spacing=9)
        page.title("Check this is right, then start",
                   "Nothing has been changed yet. This is what will happen when you press the blue button.")
        lines = [(self.machine(i), self._what_happens(i), collector_label(self.effective_collector(i))) for i in ids]
        listing = ListBox(max_height=236, name="review-list")
        if len(ids) > 3:
            groups: List[list] = []
            for machine, what, where in lines:
                match = next((g for g in groups if g[0] == what and g[1] == where), None)
                if match is None:
                    match = [what, where, []]
                    groups.append(match)
                match[2].append(machine)
            for what, where, members in groups:
                row = Row(margins=(14, 6, 14, 6), object_name=f"review-group-{len(members)}")
                who = esc(members[0].name) if len(members) == 1 else plural(len(members), "machine")
                row.say(f"<b>{who}:</b> {esc(what)} Data goes to <b>{esc(where)}</b>.")
                if len(members) > 1:
                    row.say(esc(", ".join(m.name for m in members)), "why")
                listing.add_row(row)
        else:
            for machine, what, where in lines:
                row = Row(machine.name, machine.os_name, name_width=170, margins=(14, 6, 14, 6),
                          object_name=f"review-{machine.name}")
                row.say(f"{esc(what)} Data goes to <b>{esc(where)}</b>.")
                listing.add_row(row)
        page.add(listing)
        chosen = bool(self.coll_all or any(i in self.coll for i in ids))
        if chosen:
            how = "You chose where the data goes."
        elif win.settings.collector != AUTO:
            how = "This is the collector you chose in Settings, New setups."
        else:
            how = f"The app picked one {'this machine' if one else 'each machine'} can reach."
        page.body.addLayout(sentence(f"{COLLECTOR_IS} {how}", link(
            "Send the data somewhere else", self._choose_collector_for_all, name="review-collector")))
        left = self.left_out()
        if left:
            it = "it" if len(left) == 1 else "them"
            listed = "; ".join(f"{esc(x.name)}: {esc(x.why.rstrip('.'))}" for x in left)
            page.add(Banner("warn", f"<b>Left out of this run ({len(left)}):</b> {listed}. Nothing will be done to {it}. "
                            f"Go back to fix {it}, or set {it} up afterwards.", name="review-left-out"))
        unwatched = self.unwatched()
        if unwatched:
            it = "it" if len(unwatched) == 1 else "them"
            listed = "; ".join(f"{name_of(k)} on {', '.join(self.found_on(k))}" for k in unwatched)
            page.add(Banner("warn", f"<b>Found but not watched:</b> {esc(listed)}. Only the basics will be collected there. "
                            f"You can add {it} now or later from the machine's page.",
                            button("Add now", self._edit_collect, name="review-add-now"), name="review-unwatched"))
        card = Card()
        head = hbox(label(("IT WILL COLLECT" if one else "EACH MACHINE WILL COLLECT"), "card-title", wrap=False), stretch)
        if self.retry and not self.edit_collect:
            head.addWidget(link("Change what is collected", self._edit_collect, name="review-change-collect"))
        card.body.addLayout(head)
        mixed = any(self.machine(i).os != "windows" for i in ids)
        any_windows = any(self.machine(i).os == "windows" for i in ids)
        tags: list = []
        for key in self.choices.keys:
            group = catalog_item(key).group
            if group == GROUP_SOFTWARE:
                continue
            if key in WINDOWS_ONLY:
                if any_windows:
                    tags.append(name_of(key) + (" (Windows machines)" if mixed else ""))
            else:
                tags.append(name_of(key))
        if one and ids and self.machine(ids[0]).os == "windows":
            tags = names_of(k for k in effective_keys(self.choices.keys, "windows")
                            if catalog_item(k).group != GROUP_SOFTWARE)
        card.add(Tags(tags) if tags else label("None of the basics.", "small"))
        software = [k for k in self.choices.keys if catalog_item(k).group == GROUP_SOFTWARE]
        if software:
            soft_tags = []
            for key in software:
                where = self.found_on(key)
                if one or key == "ping":
                    soft_tags.append(name_of(key))
                else:
                    soft_tags.append(f"{name_of(key)} ({', '.join(where) if where else 'no machine in this run has it'})")
            card.body.addLayout(hbox(label("And, because it runs there:" if one else "And only where it runs:", "small",
                                           wrap=False), (Tags(soft_tags), 1), spacing=8, align=Qt.AlignTop))
        if self.choices.custom_toml.strip():
            card.add(label("Plus your own Telegraf settings.", "small"))
        if self.retry and not self.edit_collect:
            card.add(label("The same choices as the last attempt.", "small", name="review-same"))
        page.add(card)
        page.body.addLayout(sentence(
            f"Takes about a minute per machine. Nothing else on {'the machine' if one else 'the machines'} is touched, "
            "and you can remove monitoring later from a machine's page.",
            link("See the exact settings files", self._show_files, name="review-files")))
        page.end()
        target = self.machine(ids[0]).name if one else plural(len(ids), "machine")
        page.footer(
            button(BACK, self.back, name="flow-back"), stretch,
            button("Practice run (changes nothing)", lambda: self.start_run(True), name="flow-practice",
                   tip="Goes through every step except the ones that change a machine"),
            button(f"Start setup on {target}", lambda: self.start_run(False), "primary big", name="flow-next"))
        self.show(page)

    def _edit_collect(self) -> None:
        self.edit_collect = True
        self.show_collect()

    def _show_change_review(self) -> None:
        machine = self.machine(self.ready()[0])
        after = effective_keys(self.choices.keys, machine.os)
        add = [k for k in after if k not in self.before]
        remove = [k for k in self.before if k not in after]
        keep = [k for k in after if k in self.before]
        self.diff = (add, remove)
        page = Page("review")
        page.title("Check the change, then apply it",
                   "Nothing has been changed yet. This is what will happen when you press the blue button.")
        card = Card(f"What changes on {machine.name}")
        nothing = not add and not remove
        if nothing:
            text = "You have not changed anything yet. Go back and tick or untick something, or cancel."
            if machine.os == "windows" and ordered(self.choices.keys) != ordered(self.before):
                text = ("Nothing changes. On a Windows machine the basics are collected together as one set, so unticking "
                        "one of them changes nothing. Go back and untick all of the basics to stop collecting them, or "
                        "cancel.")
            card.add(Banner("info", text, name="review-nothing"))
        else:
            for title, keys, kind, prefix in (("Starts collecting", add, "add", "+ "), ("Stops collecting", remove, "rem", ""),
                                              ("Stays the same", keep, "", "")):
                if not keys and kind:
                    continue
                heading = label(f"<b>{title}</b>", "plain", wrap=False, name=f"review-{kind or 'keep'}")
                heading.setFixedWidth(128)
                tags = Tags([(prefix + name, kind) for name in names_of(keys)]) if keys else label("Nothing.", "small")
                card.body.addLayout(hbox(heading, (tags, 1), spacing=8, align=Qt.AlignTop))
        page.add(card)
        page.body.addLayout(sentence(
            f"Telegraf on {esc(machine.name)} is restarted to pick up the change, which takes a few seconds. Nothing else "
            "on the machine is touched.", link("See the exact settings files", self._show_files, name="review-files")))
        page.end()
        back = button(BACK, self.back, name="flow-back")
        if nothing:
            page.footer(back, stretch, label("Nothing to apply.", "note", name="review-nothing-note"))
        else:
            page.footer(back, stretch,
                        button("Practice run (changes nothing)", lambda: self.start_run(True), name="flow-practice",
                               tip="Goes through every step except the ones that change the machine"),
                        button(f"Apply the change to {machine.name}", lambda: self.start_run(False), "primary big",
                               name="flow-next"))
        self.show(page)

    def _show_files(self) -> None:
        """The exact files, for people who want the detail."""
        parts = []
        for machine_id in self.ready():
            plan = self.plan_for(machine_id)
            machine = self.machine(machine_id)
            folder = "C:\\telegraf\\telegraf.d" if plan.windows else "/etc/telegraf/telegraf.d"
            sep = "\\" if plan.windows else "/"
            inputs = TelegrafRenderer.render_system_inputs(placeholder_monitoring(plan))
            output = TelegrafRenderer.render_vcf_output(
                collector_address=plan.collector.address, hostname=machine.name, is_windows=plan.windows,
                vm_mor=plan.vm_mor, vc_id=plan.vc_id, verify_ssl=self.win.conn.agent_verify_ssl,
                ca_cert_path=f"{folder}{sep}ca.pem", cert_path=f"{folder}{sep}cert.pem", key_path=f"{folder}{sep}key.pem")
            parts.append(f"=== {machine.name} ===\n\n# {folder}{sep}vcf-helper-system.conf (what to collect)\n{inputs}\n"
                         f"# {folder}{sep}cloudproxy-http.conf (where to send it)\n{output}")
        dialogs.show_text(self.win, "The exact settings files", "\n\n".join(parts),
                          "For people who want the detail. You never need to read this.", name="files-dialog")

    def _choose_collector_for_all(self) -> None:
        """Offer every collector, each tested from the machines in this run."""
        win = self.win
        ids = self.ready()
        names = [self.machine(i).name for i in ids]
        page = Page("testing-collectors")
        page.title(f"Testing each collector from {'this machine' if len(ids) == 1 else 'these machines'}{DOTS}",
                   COLLECTOR_IS)
        page.add(busy_bar(420)).end()
        self.busy = True
        self.show(page)
        generation = self._generation

        def work() -> Dict[str, List[bool]]:
            reach: Dict[str, List[bool]] = {}
            for machine_id in ids:
                executor = self._executor_for(machine_id)
                try:
                    windows = self.machine(machine_id).os == "windows"
                    reach[machine_id] = [can_reach(executor, c, windows) for c in win.collectors]
                finally:
                    executor.close()
            return reach

        def done(reach: Dict[str, List[bool]]) -> None:
            self.busy = False
            if not self._alive or generation != self._generation or win.flow is not self:
                return
            self.show_review()
            choices = []
            for index, collector in enumerate(win.collectors):
                cannot = [self.machine(i).name for i in ids if not reach.get(i, [False] * len(win.collectors))[index]]
                choices.append((collector, cannot))
            current = self.effective_collector(ids[0]) if len({collector_key(self.effective_collector(i)) for i in ids}) == 1 \
                else None
            picked = dialogs.collector_dialog(win, names, choices, current)
            if picked is None:
                return
            self.coll_all = picked
            self.coll = {}
            self.show_review()
            win.toast(f"Data will go to {collector_label(picked)}.")

        def failed(_exc: Exception) -> None:
            self.busy = False
            if self._alive and win.flow is self:
                self.show_review()
                win.toast("The collectors could not be tested just now. Try again in a moment.")

        win.tasks.run(work, done, failed, name="collector-test")

    # ------------------------------------------------------------------ takeover review
    def _show_takeover_review(self) -> None:
        machine = self.machine(self.ready()[0]) if self.ready() else self.machines[0]
        result = self.check[machine.id]
        steps = takeover_steps(self.version)
        page = Page("takeover-review", margins=(22, 14, 22, 12), spacing=8)
        page.title(f"Take over {esc(machine.name)}",
                   "Today VCF Operations' own agent (its built-in monitoring program) collects from this machine. A takeover "
                   "replaces it with open-source Telegraf, which this app controls. Nothing has been changed yet.")
        state = self.practice_state
        if state is not None:
            if state.get("done") and state.get("ok"):
                page.add(Banner("ok", "<b>Practice run passed. Nothing was changed.</b> The current settings can be read and "
                                "saved, the replacement settings are valid, and the machine can reach its collector. The "
                                "takeover is ready to go.", name="takeover-practice"))
            elif state.get("done"):
                page.add(Banner("bad", f"<b>The practice run found a problem. Nothing was changed.</b><br>"
                                f"{esc(state.get('what', ''))} {esc(state.get('todo', ''))}", name="takeover-practice"))
            else:
                phase = ["reading the current settings", "preparing the replacement settings",
                         "testing them without touching the machine"][min(state.get("step", 0), 2)]
                banner = Banner("info", f"<b>Practice run:</b> {phase}{DOTS}", name="takeover-practice")
                bar = busy_bar(180)
                banner.layout().addWidget(bar)
                page.add(banner)
        order = Card("What will happen, in order")
        for index, text in enumerate(steps):
            num = label(str(index + 1), "num", wrap=False)
            num.setFixedSize(22, 22)
            num.setAlignment(Qt.AlignCenter)
            note = f'<br><span style="font-size:12px">{TAKEOVER_NOTES[index]}</span>' if TAKEOVER_NOTES[index] else ""
            order.body.addLayout(hbox(num, (label(f"{esc(text)}.{note}", "plain"), 1), spacing=10, align=Qt.AlignTop))
        order.body.addStretch(1)
        same = Card("What stays the same")
        same.add(label("The machine's record and all its history in VCF Operations. It keeps collecting what it collects "
                       "today:", "plain"))
        tags: list = list(names_of(result.takeover_keys))
        if result.takeover_adds_self_check:
            tags.append(("+ a check that Telegraf itself is running", "add"))
        same.add(Tags(tags) if tags else label("The same measurements as today.", "small"))
        same.add(label("You can change what it collects afterwards, from the machine's page.", "small"))
        goes = self.effective_collector(machine.id)
        if goes is not None:
            used = bool(machine.vm.collector_address or machine.vm.collector_group)
            same.add(label(f"Its data {'keeps going' if used else 'goes'} to <b>{esc(collector_label(goes))}</b>"
                           f"{', the same collector it uses today' if used else ''}.", "small"))
        pause = Banner("warn", "<b>Monitoring of this machine pauses for about 15 minutes</b> while the swap happens. Keep "
                       "this app open until it finishes.", name="takeover-pause")
        right = box(vbox(same, pause, spacing=8))
        page.body.addLayout(hbox((order, 1), (right, 1), spacing=12, align=Qt.AlignTop))
        confirm = Card()
        typed = Field(f"To confirm, type the machine's name: {machine.name}", name="takeover-typed", value=self.typed)
        typed.input.setMaximumWidth(300)
        typed.input.textEdited.connect(lambda text: setattr(self, "typed", text))
        self.typed_field = typed
        rule = label("This is a safety check, because the old agent is removed for good.", "small")
        confirm.body.addLayout(hbox((typed, 1), (rule, 1), spacing=12, align=Qt.AlignBottom))
        page.add(confirm)
        page.end()
        practising = state is not None and not state.get("done")
        practice = button("Practice run (changes nothing)", self._takeover_practice, name="flow-practice",
                          tip="Reads the settings and prepares the replacement, without removing or installing anything")
        practice.setEnabled(not practising)
        go = button(f"Take over {machine.name}", self._takeover_start, "primary big", name="flow-next")
        go.setEnabled(not practising)
        back = button(BACK, self.back, name="flow-back")
        back.setEnabled(not practising)
        page.footer(back, stretch, practice, go)
        page.on_enter = typed.input.setFocus
        self.show(page)

    def _takeover_practice(self) -> None:
        machine_id = self.ready()[0]
        self.practice_state = {"step": 0, "done": False}
        self.busy = True
        self._show_takeover_review()
        plan = self.plan_for(machine_id, practice=True)
        generation = self._generation
        win = self.win

        def on_step(_machine_id: str, step: int) -> None:
            def apply() -> None:
                if self._alive and generation == self._generation and self.practice_state and not self.practice_state["done"]:
                    self.practice_state["step"] = step
                    if win.flow is self and self.step == REVIEW:
                        self._show_takeover_review()
            win.tasks.post(apply)

        runner = self._runner([plan], on_step=on_step)

        def done(outcomes: Dict[str, MachineOutcome]) -> None:
            self.busy = False
            if not self._alive or generation != self._generation:
                return
            outcome = outcomes[machine_id]
            self.practice_state = {"done": True, "ok": outcome.ok, "what": outcome.msg, "todo": outcome.todo}
            win.history.add(build_entry(TAKEOVER, [plan], outcomes, win.conn, practice=True))
            if win.flow is self and self.step == REVIEW:
                self._show_takeover_review()

        win.tasks.run(runner.run, done, failed=lambda _exc: done({machine_id: MachineOutcome(
            machine_id=machine_id, ok=False, msg="The practice run could not start.", todo="Try it again.")}),
            name="takeover-practice")

    def _takeover_start(self) -> None:
        machine = self.machine(self.ready()[0])
        typed = self.typed.strip()
        if typed != machine.name:
            self.typed_field.set_error(f"That does not match. Type {machine.name} exactly." if typed
                                       else "Type the machine's name to confirm.")
            self.typed_field.input.setFocus()
            return
        self.confirm_text = f"Typed '{typed}' to confirm the takeover of {machine.name} at {local_now_formatted()}"
        self.start_run(False)

    def _show_resume(self) -> None:
        machine = self.machines[0]
        record = self.record
        steps = takeover_steps(self.version)
        state = record.state if record else "retired"
        done_steps = 1 if state == "retire_failed" else 2
        when = "earlier"
        if record is not None:
            try:
                moment = datetime.strptime(record.updated_at[:19], "%Y-%m-%d %H:%M:%S")
                shown = format_when(moment.timestamp())
                when = shown[0].lower() + shown[1:] if shown.startswith(("Today", "Yesterday")) else "on " + shown
            except ValueError:
                when = "earlier"
        result = self.check.get(machine.id)
        page = Page("takeover-resume")
        blocked = bool(result and result.managed)
        page.title(f"Finish taking over {esc(machine.name)}",
                   f"The takeover stopped after step {done_steps}, {esc(when)}. The app has signed in and "
                   + ("looked at the machine." if blocked else "confirmed that this is still where things stand."))
        if blocked:
            page.add(Banner("bad", "<b>VCF Operations' own agent is on this machine again.</b><br>Something put it back "
                            "after the takeover stopped, so the app will not carry on by itself. Ask whoever runs VCF "
                            "Operations what happened, then start a new takeover from the Machines list.",
                            name="resume-blocked"))
        card = Card("Where it stopped")
        for index, text in enumerate(steps):
            finished = index < done_steps
            num = label(mark("ok") if finished else str(index + 1), "num-done" if finished else "num", wrap=False)
            num.setFixedSize(22, 22)
            num.setAlignment(Qt.AlignCenter)
            if finished:
                note = "Done. Not repeated."
            elif index == 1:
                note = ("Not confirmed: VCF Operations had not finished when the app stopped watching. The app asks VCF "
                        "Operations first, and does not ask it twice.")
            elif index == 2:
                note = "Still to do."
            else:
                note = "Still to do. Usually 10 to 15 minutes."
            card.body.addLayout(hbox(num, (label(f'{esc(text)}.<br><span style="font-size:12px">{note}</span>', "plain"), 1),
                                     spacing=10, align=Qt.AlignTop))
        page.add(card)
        page.add(Banner("info", "<b>Nothing more is removed.</b> The app only installs Telegraf and waits for data. The "
                        "machine's record and history in VCF Operations are untouched, and the copy of its old settings is "
                        "safe on this computer.", name="resume-safe"))
        page.end()
        if blocked:
            page.footer(stretch, label("Nothing to finish here.", "note"))
        else:
            page.footer(stretch, button("Finish the takeover", self._resume_start, "primary big", name="flow-next"))
        self.show(page)

    def _resume_start(self) -> None:
        self.confirm_text = (self.record.confirmation_text if self.record and self.record.confirmation_text
                             else f"Finishing the takeover of {self.machines[0].name} at {local_now_formatted()}")
        self.start_run(False)

    # ------------------------------------------------------------------ step 5: running and results
    def _runner(self, plans: List[MachinePlan], on_step=None, on_done=None, on_changing=None) -> BatchRunner:
        win = self.win

        def accept_endpoint(checks: List[str], _stage: str) -> bool:
            """The takeover asks, from its own thread, whether to carry on; the answer comes from the screen."""
            answer: Queue = Queue(maxsize=1)
            name = plans[0].name
            win.tasks.post(lambda: answer.put(dialogs.accept_endpoint_dialog(win, name, checks)))
            return bool(answer.get())

        return BatchRunner(
            plans, win.conn, win.make_adapter, win.make_executor, on_step=on_step, on_done=on_done,
            on_changing=on_changing, confirm_text=getattr(self, "confirm_text", ""),
            accept_endpoint=accept_endpoint, package_fetcher=win.package_fetcher,
            journal_dir=win.journal.directory, takeover_waits=win.takeover_waits,
        )

    def start_run(self, practice: bool) -> None:
        self._prime()
        ids = self.ready() if not self.resume else list(self.ids)
        if not ids:
            return
        takeover = self.kind == TAKEOVER
        run = RunState(ids, 4 if takeover else 5, practice)
        run.plans = [self.plan_for(i, practice) for i in ids]
        self.run = run
        self.step = RUN
        self._generation += 1
        generation = self._generation
        win = self.win

        def guarded(call):
            def wrapper(*args):
                def apply() -> None:
                    if self._alive and generation == self._generation:
                        call(*args)
                win.tasks.post(apply)
            return wrapper

        run.runner = self._runner(run.plans, on_step=guarded(self._on_step), on_done=guarded(self._on_done),
                                  on_changing=guarded(self._on_changing))
        self._show_running()
        win.tasks.run(run.runner.run, done=lambda _o: self._finish_run(generation),
                      failed=lambda _e: self._finish_run(generation), name="run")

    def _on_step(self, machine_id: str, step: int) -> None:
        run = self.run
        if run is None or run.finished or machine_id in run.outcomes:
            return
        run.step[machine_id] = max(run.step.get(machine_id, -1), step)
        self._update_running()

    def _on_changing(self, machine_id: str) -> None:
        if self.run is not None:
            self.run.changing.add(machine_id)
            self._update_running()

    def _on_done(self, machine_id: str, outcome: MachineOutcome) -> None:
        run = self.run
        if run is None:
            return
        run.outcomes[machine_id] = outcome
        run.changing.discard(machine_id)
        run.step[machine_id] = run.total
        self._update_running()

    def stop_run(self) -> None:
        run = self.run
        if run is None or run.finished or run.stopping or run.runner is None:
            return
        run.stopping = True
        run.runner.stop()
        self._update_running()

    def _can_stop(self) -> bool:
        run = self.run
        if run is None or run.stopping or run.finished or self.kind == TAKEOVER:
            return False
        waiting = [i for i in run.ids if i not in run.outcomes]
        if run.practice:
            return bool(waiting)
        return any(i not in run.changing and run.step[i] < SAFE_STEPS for i in waiting)

    def _install_word(self, machine_id: str) -> str:
        return self.install_for(machine_id)

    def _show_running(self) -> None:
        run = self.run
        if self.kind == TAKEOVER:
            self._show_takeover_running()
            return
        page = Page("running")
        title = "Practice run in progress" if run.practice else (
            "Applying the change" if self.kind == CHANGE else "Setting up monitoring")
        page.title(f"{title}{DOTS}", " ")
        self.run_sub = page.findChild(QWidget, "page-sub")
        listing = ListBox(max_height=440, name="run-list")
        self.run_rows: Dict[str, tuple] = {}
        for machine_id in run.ids:
            machine = self.machine(machine_id)
            row = Row(machine.name, machine.os_name, object_name=f"run-{machine.name}")
            text = row.say("", "small")
            bar = Progress()
            row.add_middle(bar)
            self.run_rows[machine_id] = (text, bar)
            listing.add_row(row)
        page.add(listing).end()
        self.run_note = label("", "note", name="run-note")
        # One button for the whole run: it is never rebuilt, only shown or hidden
        self.run_stop = button("Stop", self.stop_run, name="run-stop")
        page.footer((self.run_note, 1), self.run_stop)
        self.show(page)
        self._update_running()

    def _update_running(self) -> None:
        run = self.run
        if run is None or run.finished or self.win.flow is not self or self.step != RUN:
            return
        if self.kind == TAKEOVER:
            self._update_takeover_running()
            return
        try:
            done = sum(1 for i in run.ids if i in run.outcomes and not run.outcomes[i].skipped)
            if run.practice:
                pace = "A practice run takes under a minute per machine."
            elif self.kind == CHANGE:
                pace = "This takes under a minute."
            else:
                pace = "Each machine takes about a minute."
            self.run_sub.setText(f"{done} of {plural(len(run.ids), 'machine')} finished. Keep this app open; you can use "
                                 f"the computer for other things. {pace}")
            for machine_id, (text, bar) in self.run_rows.items():
                outcome = run.outcomes.get(machine_id)
                step = run.step[machine_id]
                if outcome is not None and outcome.skipped:
                    text.setText(f"{mark('skip')} Not started: you stopped the run")
                    bar.hide()
                elif outcome is not None:
                    text.setText(f"{mark('ok')} Finished" if outcome.ok else f"{mark('bad')} Finished, with a problem")
                    bar.hide()
                elif step < 0:
                    text.setText(f"{mark('idle')} Waiting its turn")
                    bar.hide()
                else:
                    words = step_label(self.kind, min(step, run.total - 1), run.practice, self._install_word(machine_id))
                    text.setText(f"{words}{DOTS}")
                    bar.show()
                    bar.set_fraction((step + 0.4) / run.total)
            can_stop = self._can_stop()
            one = len(run.ids) == 1
            self.run_stop.setText("Stop" if (one or run.practice) else "Stop the machines not yet changed")
            self.run_stop.setVisible(can_stop)
            if run.stopping:
                note = "Stopping: a machine that is already being changed finishes; the rest are left as they were."
            elif run.practice:
                note = ("This is a practice run. Nothing is changed on any machine at any step. You can stop it whenever you "
                        "like.")
            elif one:
                note = ("Nothing has been changed on the machine yet. You can still stop." if can_stop else
                        "The machine is being changed now. It cannot be stopped half-way; it will finish shortly.")
            else:
                note = "Two machines are done at a time. A problem on one does not stop the others."
            self.run_note.setText(note)
        except RuntimeError:
            pass  # the screen was replaced while an answer was on its way

    def _show_takeover_running(self) -> None:
        run = self.run
        machine = self.machine(run.ids[0])
        page = Page("takeover-running")
        page.title(f"{'Finishing the takeover of' if self.resume else 'Taking over'} {esc(machine.name)}{DOTS}",
                   "Keep this app open. You can use the computer for other things. If the app does close, open it again and "
                   "it will offer to finish from where it stopped.")
        card = Card()
        self.tk_rows = []
        for index, text in enumerate(takeover_steps(self.version)):
            num = label(str(index + 1), "num", wrap=False)
            num.setFixedSize(22, 22)
            num.setAlignment(Qt.AlignCenter)
            words = label(esc(text) + ".", "plain")
            card.body.addLayout(hbox(num, (words, 1), spacing=10, align=Qt.AlignTop))
            self.tk_rows.append((num, words, text))
        self.tk_bar = Progress()
        card.add(6, self.tk_bar)
        page.add(card).end()
        self.run_note = label("", "note", name="run-note")
        page.footer((self.run_note, 1))
        self.show(page)
        self._update_takeover_running()

    def _update_takeover_running(self) -> None:
        run = self.run
        try:
            step = max(run.step[run.ids[0]], 2 if self.resume else 0)
            from vcf_ops_telegraf_helper.gui.widgets import set_class

            for index, (num, words, text) in enumerate(self.tk_rows):
                if index < step:
                    num.setText(mark("ok"))
                    set_class(num, "num-done")
                    words.setText(esc(text) + ".")
                elif index == step:
                    set_class(num, "num-now")
                    slow = ('<br><span style="font-size:12px">This is the slow part: usually 10 to 15 minutes.</span>'
                            if index == 3 else "")
                    words.setText(f"<b>{esc(text)}{DOTS}</b>{slow}")
                else:
                    words.setText(esc(text) + ".")
            self.tk_bar.set_fraction(step / 4)
            note = f"Step {min(step + 1, 4)} of 4."
            if step == 1:
                note += " It cannot be stopped now, because the old agent is being removed."
            elif step > 1:
                note += " It cannot be stopped now, because the old agent has been removed."
            self.run_note.setText(note)
        except RuntimeError:
            pass

    def _finish_run(self, generation: int) -> None:
        run = self.run
        if run is None or run.finished or generation != self._generation:
            return
        win = self.win
        run.finished = True
        for plan in run.plans:
            run.outcomes.setdefault(plan.machine_id, MachineOutcome(
                machine_id=plan.machine_id, ok=False, msg="The run ended before this machine was finished.",
                todo="Press Try again."))
        left = self.left_out() if (self.kind == SETUP) else []
        add, remove = getattr(self, "diff", ([], [])) if self.kind == CHANGE else ([], [])
        entry = build_entry(self.kind, run.plans, run.outcomes, win.conn, left_out=left, practice=run.practice,
                            resume=self.resume, added=add, removed=remove)
        run.entry = entry
        win.history.add(entry)
        if not run.practice:
            for plan in run.plans:
                outcome = run.outcomes[plan.machine_id]
                if outcome.skipped:
                    continue
                if outcome.ok:
                    facts = dict(collector=plan.collector_label, port=plan.port, winrm_ssl=plan.winrm_ssl)
                    if outcome.collecting is not None:
                        facts["collecting"] = list(outcome.collecting)
                    if outcome.telegraf:
                        facts["telegraf"] = outcome.telegraf
                    win.history.remember(plan.machine_id, **facts)
                    if outcome.pending:
                        win.arrived.discard(plan.machine_id)
                    else:
                        win.arrived.add(plan.machine_id)
                else:
                    win.arrived.discard(plan.machine_id)
        win.refresh_statuses()
        if win.flow is self:
            self._show_result()
        if not run.practice:
            # Bring the list up to date behind the result, so it agrees with what this screen says
            win.reload_machines(then=self._refresh_result, quiet=True)

    def _refresh_result(self) -> None:
        if self._alive and self.win.flow is self and self.run and self.run.finished and self.step == RUN \
                and self.win.page is not None and self.win.page.name == "result":
            self._show_result()

    def _show_result(self) -> None:
        win = self.win
        run = self.run
        entry = run.entry
        page = Page("result", margins=(22, 14, 22, 12), spacing=9)
        page.entry_id = entry.id  # type: ignore[attr-defined]
        page.add(live_headline(entry))
        if self.kind == TAKEOVER and not run.practice:
            checks = entry.choices.get("takeover_checks") or []
            if checks:
                card = Card("The four things that had to be true")
                cells = []
                for text, state in checks:
                    symbol = {"ok": mark("ok"), "wait": mark("wait"), "bad": mark("bad")}[state]
                    suffix = "" if state == "ok" else (" (not confirmed yet)" if state == "wait" else " (did not happen)")
                    cells.append(label(f"{symbol} {esc(text)}{suffix}", "plain"))
                card.add(grid(2, cells, spacing=6))
                folder = entry.choices.get("backup_dir")
                if folder:
                    card.body.addLayout(sentence(
                        "A copy of the old settings is kept on this computer in case you need it.",
                        link("Show the folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))),
                             name="result-backup"), width=680))
                page.add(card)
        command = command_box(win, entry)
        rows = result_list(win, entry, max_height=250 if command is not None else 420)
        rows.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        page.add(rows)
        if command is not None:
            page.add(command)
        saved = ("This practice run is saved under <b>Activity</b> too." if run.practice
                 else "This result is saved. You can find it later under <b>Activity</b>.")
        page.body.addLayout(sentence(saved, link("Technical details", lambda: dialogs.show_text(
            win, "Technical details", technical_text(entry),
            "For people who want the detail. You never need to read this.", name="technical-dialog"),
            name="result-technical")))
        page.end()
        report = button(f"Save a report{DOTS}", lambda: save_report(win, entry), name="result-report")
        origin_screen, origin_machine, _entry = self.origin
        single = len(self.ids) + len(self.dropped) == 1
        back = (f"Done: back to {self.machines[0].name}" if (origin_screen in ("machine", "entry") and single)
                else "Done: back to Machines")
        if run.practice:
            passed = all(r.ok for r in entry.rows) and any(r.counted for r in entry.rows)
            real = "Apply the change for real" if self.kind == CHANGE else "Start the real setup"
            if passed:
                page.footer(report, stretch, button("Stop here", self.done, name="flow-done"),
                            button(real, lambda: self.start_run(False), "primary big", name="flow-next"))
            else:
                page.footer(report, stretch, button(BACK, self._back_to_review, name="flow-back"),
                            button("Stop here", self.done, "primary big", name="flow-next"))
        else:
            page.footer(report, stretch, button(back, self.done, "primary big", name="flow-next"))
        self.show(page)

    def _back_to_review(self) -> None:
        self.run = None
        self.show_review()

    # ------------------------------------------------------------------ leaving
    def back(self) -> None:
        steps = [key for key, _text in self.steps()]
        index = steps.index(self.step) if self.step in steps else 0
        target = steps[max(0, index - 1)]
        if target == SIGNIN:
            self.auto = False
            self._generation += 1
            self.show_signin()
        elif target == CHECK:
            self.show_check()
        elif target == COLLECT:
            self.show_collect()
        else:
            self.show_review()

    def cancel(self) -> None:
        self._leave(False)

    def done(self) -> None:
        self._leave(True)

    def _leave(self, finished: bool) -> None:
        win = self.win
        self.abandon()
        win.leave_flow()
        win.filter, win.query = "all", ""
        origin_screen, origin_machine, origin_entry = self.origin
        single = len(self.ids) + len(self.dropped) == 1
        if origin_screen == "entry" and not finished and origin_entry:
            win.show_entry(origin_entry, from_machine=getattr(win, "entry_from_machine", None))
            return
        if origin_screen in ("machine", "entry") and single:
            win.show_machine(self.ids[0] if self.ids else self.dropped[0])
            return
        banner = None
        run = self.run
        if finished and run and run.finished and not run.practice and run.entry is not None:
            waiting = sum(1 for r in run.entry.rows if r.pending)
            more = (f" {plural(waiting, 'machine is', 'machines are')} waiting for first data; press \"Refresh list\" in a "
                    "few minutes.") if waiting else ""
            banner = ("ok", f"<b>{esc(run.entry.title)} finished.</b> The list below shows where each machine stands now."
                      f"{more} The full result is saved under Activity.")
        win.show_machines(banner)
