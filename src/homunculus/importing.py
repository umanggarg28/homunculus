"""Copying the operator's own files into memory as raw sources, after a scan.

The operator runs this themselves, as `homunculus import`. It reads the list they
wrote in `import.txt`, leaves out sensitive categories by name, scans each remaining
file, shows all of it, and copies only after they agree. The agent and the server
never see the operator's folders: only this command does.

The scan uses maintained tools rather than patterns of our own: Microsoft Presidio for
personal data (emails, phones, card and bank numbers, identity numbers, each checked by
its pattern, its checksum where it has one, and the words around it), and gitleaks for
keys. The operator's own identifiers are matched as written.

What it can and cannot catch: categories such as health or immigration are caught by
the words in a file's path, because reading a file's meaning would need a model, and
nothing goes to a model before the operator has looked.

Every setting is in `import-rules.toml` in the data directory, written with the
defaults below on first run, so the operator can see and change each one.
"""

import dataclasses
import hashlib
import json
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homunculus import identifiers
from homunculus.identifiers import mask
from homunculus.memory import Memory

if TYPE_CHECKING:
    from presidio_analyzer import AnalyzerEngine

LIST_FILE = "import.txt"
RULES_FILE = "import-rules.toml"
LIST_TEMPLATE = """\
# Files to copy into the agent's memory, one path or pattern per line.
# ~ is your home folder, * matches within a folder, ** matches any depth.
# A line starting with ! leaves files out. Nothing is copied until you agree.
#
# ~/Notes/**/*.md
# !~/Notes/private/**
"""
# How a Presidio entity is named in the report.
KINDS = {
    "EMAIL_ADDRESS": "email",
    "PHONE_NUMBER": "phone",
    "CREDIT_CARD": "card number",
    "IBAN_CODE": "bank account",
}


@dataclass(frozen=True)
class Rules:
    """How an import decides what to leave out, read from import-rules.toml."""

    # Only text the scan can read is imported.
    types: tuple[str, ...] = (
        ".md", ".markdown", ".txt", ".text", ".csv", ".tsv", ".json", ".yaml", ".yml",
        ".toml", ".html", ".htm", ".xml", ".org", ".rst", ".tex",
    )  # fmt: skip
    max_mb: float = 1
    # Words in a path that keep a file out: the spec's sensitive categories, by name.
    excluded: tuple[str, ...] = (
        "visa", "visas", "immigration", "passport", "passports", "residence", "asylum",
        "bank", "banking", "tax", "taxes", "salary", "payslip", "payslips", "payroll",
        "statement", "statements", "invoice", "invoices", "loan", "mortgage",
        "aadhaar", "ssn", "identity", "kyc",
        "password", "passwords", "credential", "credentials", "secret", "secrets", "keychain",
        "medical", "health", "prescription", "prescriptions", "diagnosis", "therapy", "insurance",
    )  # fmt: skip
    # Presidio's names for what the scan looks for.
    entities: tuple[str, ...] = (
        "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "IBAN_CODE", "US_SSN",
        "IN_AADHAAR", "IN_PAN", "IN_PASSPORT", "IN_VOTER",
    )  # fmt: skip
    # Countries whose phone numbers are found without a + country code.
    phone_regions: tuple[str, ...] = ("IN", "US", "GB")
    # Presidio's confidence, from 0 to 1, at which a match is reported.
    min_score: float = 0.4


def _toml_list(values: tuple[str, ...], per_line: int = 6) -> str:
    rows = [values[i : i + per_line] for i in range(0, len(values), per_line)]
    body = ",\n".join("  " + ", ".join(f'"{v}"' for v in row) for row in rows)
    return f"[\n{body},\n]"


def rules_template(rules: Rules) -> str:
    return f"""\
# How `homunculus import` decides what to leave out. Change anything here; a setting
# you delete goes back to its default.

[files]
# Only these file types are imported: the scan can only check text it can read.
types = {_toml_list(rules.types, 8)}
max_mb = {rules.max_mb:g}

[names]
# A file is left out when one of these words is in its path, such as Notes/visa/renewal.md.
# Categories like health or immigration are caught this way: reading a file's meaning
# would need a model, and nothing goes to a model before you have looked.
excluded = {_toml_list(rules.excluded)}

[scan]
# What the scan looks for, by Presidio's names:
# https://microsoft.github.io/presidio/supported_entities/
entities = {_toml_list(rules.entities, 5)}
# Countries whose phone numbers are found without a + country code.
phone_regions = {_toml_list(rules.phone_regions)}
# How sure the scan must be, from 0 to 1, before it reports a match.
min_score = {rules.min_score:g}
"""


def load_rules(home: Path) -> Rules:
    """The rules, written out with the defaults on first use."""
    path = home / RULES_FILE
    if not path.exists():
        path.write_text(rules_template(Rules()), encoding="utf-8")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    found: dict[str, Any] = {
        **data.get("files", {}),
        **data.get("names", {}),
        **data.get("scan", {}),
    }
    # A setting left out of the file keeps its default; lists become tuples, like the defaults.
    changed: dict[str, Any] = {
        name: tuple(value) if isinstance(value, list) else value
        for name, value in found.items()
        if name in Rules.__dataclass_fields__
    }
    return dataclasses.replace(Rules(), **changed)


def now() -> datetime:
    return datetime.now()


@dataclass(frozen=True)
class Finding:
    """Something sensitive in a file: what kind, on which line, and a masked copy."""

    kind: str
    line: int
    shown: str


@dataclass
class Plan:
    """What an import would do, shown in full before anything is copied."""

    copy: list[tuple[Path, str]] = field(default_factory=list)  # (file, where it goes)
    left_out: list[tuple[str, str]] = field(default_factory=list)  # (name, why)
    already: list[str] = field(default_factory=list)


def run(home: Path, allowed: list[str]) -> int:
    """The `import` command. Returns the exit code."""
    list_file = home / LIST_FILE
    identifiers.ensure(home)
    rules = load_rules(home)
    if not list_file.exists():
        list_file.write_text(LIST_TEMPLATE, encoding="utf-8")
        print(f"Write the files to import in {list_file}, then run this again.")
        return 0
    # Keys are found by gitleaks. Without it the scan would be incomplete, so nothing runs.
    if shutil.which("gitleaks") is None:
        print("Importing needs gitleaks to scan for keys. Install it with: brew install gitleaks")
        return 1

    memory = Memory.open(home / "memory")
    plan = make_plan(home, memory, rules, {Path(p).expanduser().resolve() for p in allowed})
    show(plan)
    if not plan.copy:
        print("Nothing to import.")
        return 0
    count = _files(len(plan.copy))
    if input(f"Copy {count} into memory? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing copied.")
        return 0
    folder = copy(memory, plan)
    print(f"Imported {count} into memory/{folder}.")
    return 0


def make_plan(home: Path, memory: Memory, rules: Rules, allowed: set[Path]) -> Plan:
    plan = Plan()
    words = {w.lower() for w in rules.excluded}
    types = {t.lower() for t in rules.types}
    mine = identifiers.load(home)
    engine: AnalyzerEngine | None = None
    known = {_digest(p.read_bytes()) for p in (memory.root / "raw").rglob("*") if p.is_file()}
    for file, name in resolve(_list(home / LIST_FILE)):
        word = excluded_by(name, words)
        if word:
            plan.left_out.append((name, f'excluded by name: "{word}"'))
            continue
        if file.suffix.lower() not in types:
            plan.left_out.append((name, "not a text file"))
            continue
        data = file.read_bytes()
        if len(data) > rules.max_mb * 1_000_000:
            plan.left_out.append((name, f"over {rules.max_mb:g} MB"))
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            plan.left_out.append((name, "not a text file"))
            continue
        if _digest(data) in known:
            plan.already.append(name)
            continue
        engine = engine or analyzer(rules)
        findings = scan(text, mine, engine, rules) + keys(file)
        if findings and file.resolve() not in allowed:
            listed = ", ".join(f"{f.kind} {f.shown} (line {f.line})" for f in findings)
            plan.left_out.append((name, f"{listed}. Use --allow {file} to include it."))
            continue
        known.add(_digest(data))
        plan.copy.append((file, name))
    return plan


def resolve(lines: list[str]) -> list[tuple[Path, str]]:
    """The files a list names, each with the name it keeps in memory: the folder the
    pattern starts from, then the path inside it ("Notes/visa/renewal.md")."""
    chosen: dict[Path, str] = {}
    dropped: set[Path] = set()
    for line in lines:
        leave_out = line.startswith("!")
        for file, name in _expand(line[1:] if leave_out else line):
            if leave_out:
                dropped.add(file)
            else:
                chosen.setdefault(file, name)
    return [(file, name) for file, name in chosen.items() if file not in dropped]


def _expand(pattern: str) -> list[tuple[Path, str]]:
    path = Path(pattern).expanduser()
    parts = path.parts
    first = next((i for i, part in enumerate(parts) if any(c in part for c in "*?[")), None)
    if first is None:
        if path.is_dir():
            base, files = path, sorted(path.rglob("*"))
        else:
            base, files = path.parent, [path]
    else:
        base = Path(*parts[:first])
        files = sorted(base.glob(str(Path(*parts[first:]))))
    found = []
    for file in files:
        # A link could point anywhere; only real files the list names are copied.
        if file.is_symlink() or not file.is_file():
            continue
        found.append((file.resolve(), f"{base.name}/{file.relative_to(base).as_posix()}"))
    return found


def excluded_by(name: str, words: set[str]) -> str | None:
    """The first word of the path that is on the excluded list, if any."""
    for token in re.split(r"[^a-z0-9]+", name.lower()):
        if token in words:
            return token
    return None


def analyzer(rules: Rules) -> "AnalyzerEngine":
    """Presidio, with the small English model, the India recognizers it ships but does
    not turn on by default, and phone numbers for the chosen countries. Loaded only when
    an import runs, so the agent's server starts without it."""
    import tldextract
    from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_analyzer.predefined_recognizers import (
        InAadhaarRecognizer,
        InPanRecognizer,
        InPassportRecognizer,
        InVoterRecognizer,
        PhoneRecognizer,
    )

    # Presidio's email check uses tldextract, which by default downloads the public suffix
    # list on first use. An import reaches no network: use the copy bundled with the package.
    tldextract.tldextract.TLD_EXTRACTOR = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=())
    nlp = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
    ).create_engine()
    registry = RecognizerRegistry(supported_languages=["en"])
    registry.load_predefined_recognizers(languages=["en"], nlp_engine=nlp)
    registry.remove_recognizer("PhoneRecognizer")
    registry.add_recognizer(PhoneRecognizer(supported_regions=rules.phone_regions))
    for recognizer in (
        InAadhaarRecognizer(),
        InPanRecognizer(),
        InPassportRecognizer(),
        InVoterRecognizer(),
    ):
        registry.add_recognizer(recognizer)
    return AnalyzerEngine(nlp_engine=nlp, registry=registry, supported_languages=["en"])


def scan(text: str, mine: list[str], engine: "AnalyzerEngine", rules: Rules) -> list[Finding]:
    """What in `text` should not reach the model without the operator's say."""
    found = [
        Finding(
            KINDS.get(hit.entity_type, "identity number"),
            text.count("\n", 0, hit.start) + 1,
            mask(text[hit.start : hit.end]),
        )
        for hit in engine.analyze(
            text=text, language="en", entities=list(rules.entities), score_threshold=rules.min_score
        )
    ]
    found.sort(key=lambda f: f.line)
    for line, identifier in identifiers.find(text, mine):
        found.append(Finding("your identifier", line, mask(identifier)))
    return found


def keys(file: Path) -> list[Finding]:
    """Keys and passwords, found by gitleaks with its own maintained rules."""
    result = subprocess.run(
        ["gitleaks", "dir", str(file), "--report-format", "json", "--report-path", "-",
         "--no-banner", "--log-level", "error", "--exit-code", "0"],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return [
        Finding(f"key ({leak['RuleID']})", leak["StartLine"], mask(leak.get("Secret") or ""))
        for leak in json.loads(result.stdout or "[]")
    ]


def show(plan: Plan) -> None:
    if plan.copy:
        print(f"Import {_files(len(plan.copy))}:")
        for _, name in plan.copy:
            print(f"  {name}")
    if plan.left_out:
        print("Left out:")
        for name, why in plan.left_out:
            print(f"  {name}    {why}")
    if plan.already:
        print("Already imported, unchanged:")
        for name in plan.already:
            print(f"  {name}")


def copy(memory: Memory, plan: Plan) -> str:
    """Copy the planned files into a folder for this import, and record it. Returns the
    folder, relative to memory."""
    stamp = now().strftime("%Y-%m-%d-%H%M")
    with memory.lock:
        folder = f"raw/imports/{stamp}"
        suffix = 1
        while (memory.root / folder).exists():
            suffix += 1
            folder = f"raw/imports/{stamp}-{suffix}"
        landed = []
        for file, name in plan.copy:
            target = memory.root / folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(file.read_bytes())
            landed.append(f"{folder}/{name}")
        memory.record_import(landed)
    return folder


def _list(path: Path) -> list[str]:
    if not path.is_file():
        return []
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _files(n: int) -> str:
    return f"{n} file" if n == 1 else f"{n} files"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
