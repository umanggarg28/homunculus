"""When the agent works on its own.

Four things wake it: an hourly timer, a wake-up it set for itself with `wake_at`, a
routine, and the operator pressing Wake now. A wake-up starts a fresh job: the agent
reads the goals and its to-do notes, does the next useful piece of work, and keeps the
notes current. A routine names a skill, which the job is handed directly, so recurring
work such as a morning brief does not rest on the model choosing it.

The waker only decides when. Whether a job may start (the budget), how long it may run
(the job limits) and what it may do (the gate) are decided where they are for every job.
Its state is a small JSON file in the data directory, so a pause and the agent's own
wake-ups survive a restart.

Others do the same with a heartbeat: OpenClaw and nanobot wake every 30 minutes to read
a checklist; OpenAI's Dots and Hermes let the agent schedule its own returns. Routines
are Hermes's scheduled jobs that attach a skill, with its rule for missed runs: one run
when the computer wakes, if it is not too late.
"""

import dataclasses
import json
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import mcp_types as mcp

from homunculus.limits import Limits
from homunculus.tools import Tool, ToolError

SETTINGS_FILE = "waker.toml"
TIME_FORMAT = "%Y-%m-%d %H:%M"
EARLIEST = timedelta(minutes=5)
LATEST = timedelta(days=7)
MAX_PENDING = 10

# Why a wake-up happened, in words that read the same in Activity and in the agent's
# prompt, which says "you" for a wake-up the agent set itself.
OPERATOR_ASKED = "Wake now"
TIMER = "the hourly timer"
OWN = "a wake-up it set: "
ROUTINE = "the routine "

ROUTINES_FILE = "routines.toml"
# A routine missed while the computer slept still runs if it is at most this late, so a
# morning brief is not written in the afternoon.
CATCH_UP = timedelta(hours=2)
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_SETS = {
    "every day": frozenset(range(7)),
    "weekdays": frozenset(range(5)),
    "weekends": frozenset({5, 6}),
}


@dataclass(frozen=True)
class WakerSettings:
    every_minutes: int = 60
    # An IANA name such as "Asia/Kolkata", or empty for the machine's own time zone.
    time_zone: str = ""

    def zone(self) -> tzinfo:
        if self.time_zone:
            return ZoneInfo(self.time_zone)
        local = datetime.now().astimezone().tzinfo
        assert local is not None
        return local


TEMPLATE = """\
# When the agent wakes to work on its own. Read when the agent starts.

# Minutes between wake-ups from the timer. The agent can also set its own wake-ups, and
# every wake-up counts toward wakeups_per_day in limits.toml.
every_minutes = {every_minutes}

# The time zone for wake-up times and for counting the day, such as "Asia/Kolkata".
# Empty means this computer's own time zone.
time_zone = "{time_zone}"
"""


class RoutinesError(ValueError):
    """The routines file cannot be understood; the message says where and why."""


@dataclass(frozen=True)
class Routine:
    """Run a skill at a time of day, on some days of the week, in a time zone."""

    skill: str
    at: time
    days: frozenset[int]
    zone: tzinfo

    @classmethod
    def of(cls, entry: dict[str, Any], default_zone: tzinfo, number: int = 1) -> "Routine":
        where = f"routine {number}"
        skill = str(entry.get("skill", "")).strip()
        if not skill:
            raise RoutinesError(f"{where}: name the skill it runs")
        try:
            at = datetime.strptime(str(entry.get("at", "")).strip(), "%H:%M").time()
        except ValueError:
            raise RoutinesError(f"{where}: give at as HH:MM, such as 07:00") from None
        days = _days(str(entry.get("days", "every day")))
        if days is None:
            raise RoutinesError(
                f"{where}: days must be every day, weekdays, weekends or days such as mon,thu"
            )
        name = str(entry.get("time_zone", "")).strip()
        try:
            zone = ZoneInfo(name) if name else default_zone
        except (ZoneInfoNotFoundError, ValueError):
            raise RoutinesError(f"{where}: {name} is not a time zone") from None
        return cls(skill, at, days, zone)

    @property
    def key(self) -> str:
        """Names this routine in the waker's state, apart from another running the same
        skill at another time."""
        days = ",".join(WEEKDAYS[day] for day in sorted(self.days))
        return f"{self.skill} {days} {self.at:%H:%M} {self.zone}"

    def last_time(self, now: datetime) -> datetime:
        """When it was last due, at or before `now`."""
        return max(t for t in self._times_around(now) if t <= now)

    def next_time(self, now: datetime) -> datetime:
        """When it is next due, after `now`."""
        return min(t for t in self._times_around(now) if t > now)

    def _times_around(self, now: datetime) -> list[datetime]:
        today = now.astimezone(self.zone).date()
        days = (today + timedelta(days=offset) for offset in range(-7, 8))
        return [self._on(day) for day in days if day.weekday() in self.days]

    def _on(self, day: date) -> datetime:
        return datetime.combine(day, self.at, tzinfo=self.zone)


def _days(text: str) -> frozenset[int] | None:
    text = " ".join(text.lower().split())
    if text in DAY_SETS:
        return DAY_SETS[text]
    names = [name.strip() for name in text.split(",")]
    if not names or any(name not in WEEKDAYS for name in names):
        return None
    return frozenset(WEEKDAYS.index(name) for name in names)


ROUTINES_TEMPLATE = """\
# Routines: run a skill at a set time, as work on its own. Read when the agent starts.
# Each runs within the day's wake-ups, stops while paused, and waits if you are chatting.
# One missed while the computer slept runs when it wakes, if it is at most 2 hours late.
#
# Remove the # signs below to have a brief written each weekday morning.
#
# [[routine]]
# skill = "morning-brief"     # a skill's folder name, in skills/
# at = "07:00"
# days = "weekdays"           # every day, weekdays, weekends, or days such as "mon,thu"
# time_zone = ""              # such as "Asia/Kolkata"; empty means waker.toml's
"""


def load_routines(home: Path, default_zone: tzinfo) -> list[Routine]:
    path = home / ROUTINES_FILE
    if not path.exists():
        path.write_text(ROUTINES_TEMPLATE, encoding="utf-8")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise RoutinesError(f"{ROUTINES_FILE} is not valid TOML: {error}") from None
    entries = data.get("routine", [])
    if not isinstance(entries, list):
        raise RoutinesError(f"{ROUTINES_FILE}: write each routine under [[routine]]")
    return [Routine.of(entry, default_zone, number) for number, entry in enumerate(entries, 1)]


def routine_skill(reason: str) -> str | None:
    """The skill a wake-up's reason names, if a routine woke the agent."""
    return reason.removeprefix(ROUTINE) if reason.startswith(ROUTINE) else None


def load_settings(home: Path) -> WakerSettings:
    path = home / SETTINGS_FILE
    if not path.exists():
        path.write_text(TEMPLATE.format(**dataclasses.asdict(WakerSettings())), encoding="utf-8")
    data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    known = {k: v for k, v in data.items() if k in WakerSettings.__dataclass_fields__}
    return dataclasses.replace(WakerSettings(), **known)


class Waker:
    def __init__(
        self,
        path: Path,
        settings: WakerSettings,
        limits: Limits,
        clock: Callable[[], datetime] | None = None,
        routines: list[Routine] | None = None,
    ) -> None:
        self._path = path
        self._routines = list(routines or [])
        self._settings = settings
        self._limits = limits
        self._zone = settings.zone()
        self._clock = clock or (lambda: datetime.now(self._zone))
        # Set by the server while an unprompted job runs.
        self.working = False
        self._state: dict[str, Any] = {
            "paused": False,
            "last_wake": self.now().isoformat(),
            "pending": [],
            "next_id": 1,
            "day": self._today(),
            "woken": 0,
            "skipped_busy": 0,
            "wake_now": False,
            "own_files": [],
            # Each routine's last time handled, run or missed. A routine first seen now
            # starts from now, so it never runs for a time already past.
            "routines": {},
            "missed": [],
        }
        if path.exists():
            self._state.update(json.loads(path.read_text(encoding="utf-8")))
        for routine in self._routines:
            self._state["routines"].setdefault(routine.key, self.now().isoformat())
        self._save()
        self.own_files = _OwnFiles(self)

    # The operator's controls

    def pause(self) -> None:
        self._state["paused"] = True
        self._save()

    def resume(self) -> None:
        self._state["paused"] = False
        self._save()

    def wake_now(self) -> None:
        """The operator asks for a wake-up straight away. It runs even while paused,
        because the operator asked for it, and it counts toward the day's wake-ups."""
        self._state["wake_now"] = True
        self._save()

    # Deciding

    def take(self) -> str | None:
        """Why the agent should wake now, or None. Taking a wake-up uses it up."""
        reason = self.peek()
        if reason is None:
            return None
        now = self.now()
        if reason == OPERATOR_ASKED:
            self._state["wake_now"] = False
        elif reason.startswith(ROUTINE):
            routine = self._due_routine(now)
            assert routine is not None
            self._state["routines"][routine.key] = routine.last_time(now).isoformat()
        elif reason != TIMER:
            due = self._due_wakeups(now)
            self._state["pending"].remove(due[0])
        self._state["last_wake"] = now.isoformat()
        self._state["woken"] += 1
        self._save()
        return reason

    def peek(self) -> str | None:
        """Why the agent would wake now, without using it up."""
        now = self.now()
        self._roll_day()
        if self._state["woken"] >= self._limits.wakeups_per_day:
            if self._state["wake_now"]:
                # Asked for past the day's limit: dropped, so it does not fire tomorrow.
                self._state["wake_now"] = False
                self._save()
            return None
        if self._state["wake_now"]:
            return OPERATOR_ASKED
        if self._state["paused"]:
            return None
        routine = self._due_routine(now)
        if routine is not None:
            return ROUTINE + routine.skill
        due = self._due_wakeups(now)
        if due:
            return OWN + due[0]["why"]
        if now - datetime.fromisoformat(self._state["last_wake"]) >= self._every:
            return TIMER
        return None

    def skip_busy(self) -> None:
        """The timer came round while the operator was busy: that slot is skipped, not
        queued, so the agent never catches up with a burst of wake-ups."""
        self._state["last_wake"] = self.now().isoformat()
        self._state["skipped_busy"] += 1
        self._save()

    def status(self) -> dict[str, Any]:
        self._roll_day()
        last = datetime.fromisoformat(self._state["last_wake"]).astimezone(self._zone)
        return {
            "paused": self._state["paused"],
            "working": self.working,
            "next_timer": (last + self._every).strftime(TIME_FORMAT),
            "pending": [dict(w) for w in self._pending_sorted()],
            "routines": sorted(
                (
                    {"skill": r.skill, "at": self._local(r.next_time(self.now()))}
                    for r in self._routines
                ),
                key=lambda r: r["at"],
            ),
            "missed": [dict(m) for m in self._state["missed"]],
            "today": {
                "woken": self._state["woken"],
                "cap": self._limits.wakeups_per_day,
                "skipped_busy": self._state["skipped_busy"],
            },
        }

    # The agent's tools

    def tools(self) -> list[Tool]:
        own = mcp.ToolAnnotations(
            read_only_hint=False, destructive_hint=False, open_world_hint=False
        )
        set_one = mcp.Tool(
            name="wake_at",
            title="Set a wake-up",
            description=(
                "Wake yourself at a set time to carry on with work, for example when "
                "something you wait for should be ready, or when the work is best done "
                "then. Times are the operator's local time. Returns every wake-up you "
                "have waiting; check it before adding another."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "at": {"type": "string", "description": "Local time, as YYYY-MM-DD HH:MM."},
                    "why": {"type": "string", "description": "What you will do then, briefly."},
                },
                "required": ["at", "why"],
            },
            annotations=own,
        )
        cancel_one = mcp.Tool(
            name="cancel_wake",
            title="Cancel a wake-up",
            description="Cancel one of your waiting wake-ups by its id, when plans change.",
            input_schema={
                "type": "object",
                "properties": {"id": {"type": "string", "description": "The wake-up's id."}},
                "required": ["id"],
            },
            annotations=own,
        )
        return [Tool(set_one, self._wake_at), Tool(cancel_one, self._cancel)]

    def _wake_at(self, arguments: dict[str, Any]) -> str:
        try:
            at = datetime.strptime(str(arguments["at"]).strip(), TIME_FORMAT)
        except ValueError:
            raise ToolError("Give the time as YYYY-MM-DD HH:MM, in local time.") from None
        when = at.replace(tzinfo=self._zone)
        now = self.now()
        if when - now < EARLIEST:
            raise ToolError("A wake-up must be at least 5 minutes from now.")
        if when - now > LATEST:
            raise ToolError("A wake-up must be at most 7 days from now.")
        if len(self._state["pending"]) >= MAX_PENDING:
            raise ToolError(
                f"You already have {MAX_PENDING} wake-ups waiting. Cancel one with "
                "cancel_wake before adding another.\n" + self._listing()
            )
        wakeup = {
            "id": f"w{self._state['next_id']}",
            "at": when.strftime(TIME_FORMAT),
            "why": str(arguments["why"]).strip(),
        }
        self._state["next_id"] += 1
        self._state["pending"].append(wakeup)
        self._save()
        return f"Wake-up {wakeup['id']} set.\n" + self._listing()

    def _cancel(self, arguments: dict[str, Any]) -> str:
        wanted = str(arguments["id"]).strip()
        kept = [w for w in self._state["pending"] if w["id"] != wanted]
        if len(kept) == len(self._state["pending"]):
            raise ToolError(f"There is no wake-up {wanted}.\n" + self._listing())
        self._state["pending"] = kept
        self._save()
        return f"Cancelled {wanted}.\n" + self._listing()

    def _listing(self) -> str:
        waiting = self._pending_sorted()
        if not waiting:
            return "No wake-ups waiting."
        lines = [f"- {w['id']}: {w['at']}, {w['why']}" for w in waiting]
        return "Wake-ups waiting:\n" + "\n".join(lines)

    def _own(self) -> list[str]:
        own: list[str] = self._state["own_files"]
        return own

    # Inside

    @property
    def _every(self) -> timedelta:
        return timedelta(minutes=self._settings.every_minutes)

    def now(self) -> datetime:
        """The time in the waker's time zone."""
        return self._clock().astimezone(self._zone)

    def _local(self, moment: datetime) -> str:
        return moment.astimezone(self._zone).strftime(TIME_FORMAT)

    def _due_routine(self, now: datetime) -> Routine | None:
        """The routine due now, if any. One missed by more than CATCH_UP is marked as
        missed instead, so the operator sees it did not run."""
        for routine in self._routines:
            last = routine.last_time(now)
            handled = datetime.fromisoformat(self._state["routines"][routine.key])
            if last <= handled:
                continue
            if now - last > CATCH_UP:
                self._state["routines"][routine.key] = last.isoformat()
                self._state["missed"].append({"skill": routine.skill, "at": self._local(last)})
                self._save()
                continue
            return routine
        return None

    def _today(self) -> str:
        return self.now().date().isoformat()

    def _roll_day(self) -> None:
        today = self._today()
        if self._state["day"] != today:
            self._state.update(day=today, woken=0, skipped_busy=0, missed=[])
            self._save()

    def _pending_sorted(self) -> list[dict[str, str]]:
        return sorted(self._state["pending"], key=lambda w: w["at"])

    def _due_wakeups(self, now: datetime) -> list[dict[str, str]]:
        local = now.strftime(TIME_FORMAT)
        return [w for w in self._pending_sorted() if w["at"] <= local]

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")


def wake_prompt(now: datetime, reason: str, routine: str | None = None) -> str:
    """What the agent is told when it wakes. It starts fresh: the goals and to-do notes
    in its instructions are all it carries from earlier work. A routine's wake-up also
    carries the instructions of its skill, `routine`; an empty string means the skill
    is missing."""
    opening = (
        f"It is {now.strftime('%A %-d %B %Y, %H:%M')}. You woke up on your own "
        f"({_for_the_agent(reason)}); the operator is not waiting for a reply.\n\n"
    )
    skill = routine_skill(reason)
    if skill is not None and not routine:
        return opening + (
            f"This routine runs the skill {skill}, but there is no skill named {skill}. "
            "Say so in one line, so the operator can fix the routine, and use no tools."
        )
    if skill is not None:
        task = (
            f"This routine runs the skill {skill}. Its instructions follow; carry them out "
            f"now. Paths in them are relative to skills/{skill}/.\n\n"
            f'<skill_content name="{skill}">\n{routine}\n</skill_content>\n\n'
            "Before you finish, add to memory/todo.md anything the next wake-up should know."
        )
    else:
        task = (
            "Read the goals and your to-do notes in memory/todo.md, both shown in your "
            "instructions, and do the next useful piece of work toward the goals in your "
            "workspace. Before you finish, update memory/todo.md: what you did, what is next "
            "and why. If you want to carry on at a particular time, use wake_at."
        )
    return (
        opening
        + task
        + (
            "\n\n"
            "No one asked for this work, so leave the operator's files as they are: write your "
            "drafts and notes as new files beside them. Changing a file you did not make on "
            "your own waits for their approval.\n\n"
            "If the next step needs the operator's decision, put the question at the top of "
            "memory/todo.md, say it in one line, and stop: do no other work toward that goal "
            "until they answer.\n\n"
            "If nothing is worth doing now, say so in one short line and use no tools."
        )
    )


def _for_the_agent(reason: str) -> str:
    if reason == OPERATOR_ASKED:
        return "the operator pressed Wake now"
    return reason.replace(OWN, "a wake-up you set: ", 1)


class _OwnFiles:
    """The workspace files the agent made on its own, saved with the waker's state, so
    it can go on working on its own drafts on a later wake-up."""

    def __init__(self, waker: Waker) -> None:
        self._waker = waker

    def __contains__(self, path: object) -> bool:
        return path in self._waker._own()

    def add(self, path: str) -> None:
        own = self._waker._own()
        if path not in own:
            own.append(path)
            self._waker._save()
