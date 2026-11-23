"""Importing the operator's files into memory, through the command they run.

Every file here is fictional, in temporary folders. Only the clock is faked.
"""

import io
import secrets
import string
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

import homunculus
from homunculus import importing
from homunculus.memory import Memory

CONTACTS = "Coach: riya@example.com, +91 98765 43210\n"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOMUNCULUS_HOME", str(folder))
    monkeypatch.setattr(importing, "now", lambda: datetime(2026, 11, 22, 21, 50))
    return folder


@pytest.fixture
def notes(tmp_path: Path) -> Path:
    folder = tmp_path / "Notes"
    (folder / "visa").mkdir(parents=True)
    (folder / "running-plan.md").write_text("Week 3: three runs, long run Sunday.\n")
    (folder / "contacts.md").write_text(CONTACTS)
    (folder / "visa" / "renewal.md").write_text("Renewal due in March.\n")
    return folder


@pytest.fixture
def docs(tmp_path: Path) -> Path:
    """A plainly named folder: pytest names temporary folders after the test, and a
    test's name can hold a word that is on the excluded list."""
    folder = tmp_path / "docs"
    folder.mkdir()
    return folder


Run = Callable[..., tuple[int, str]]


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Run:
    """Run the command as the operator would, answering its question with `answer`."""

    def go(*argv: str, answer: str = "y\n") -> tuple[int, str]:
        monkeypatch.setattr("sys.stdin", io.StringIO(answer))
        try:
            homunculus.main(list(argv))
            code = 0
        except SystemExit as stop:
            code = int(stop.code or 0)
        return code, capsys.readouterr().out

    return go


def import_list(home: Path, *lines: str) -> None:
    (home / "import.txt").write_text("\n".join(lines) + "\n")


def raw(home: Path) -> Path:
    return home / "memory" / "raw" / "imports" / "2026-11-22-2150"


def git(home: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=home / "memory", capture_output=True, text=True, check=True
    ).stdout


def test_the_first_run_writes_an_empty_list_and_copies_nothing(home: Path, run: Run) -> None:
    code, out = run("import")

    assert code == 0
    assert (home / "import.txt").is_file()
    assert "import.txt" in out
    assert not (home / "memory" / "raw" / "imports").exists()


def test_files_are_listed_first_and_copied_only_after_you_agree(
    home: Path, notes: Path, run: Run
) -> None:
    import_list(home, f"{notes}/running-plan.md")

    _, refused = run("import", answer="n\n")

    assert "running-plan.md" in refused
    assert not raw(home).exists()

    _, agreed = run("import", answer="y\n")

    copy = raw(home) / "Notes" / "running-plan.md"
    assert copy.read_text() == "Week 3: three runs, long run Sunday.\n"
    assert "Imported 1 file" in agreed
    assert git(home, "log", "-1", "--format=%an %s") == "Operator Import 1 file\n"
    log = (home / "memory" / "wiki" / "log.md").read_text()
    assert "import | raw/imports/2026-11-22-2150/Notes/running-plan.md" in log
    assert (notes / "running-plan.md").exists()


def test_a_file_with_findings_is_left_out_and_its_values_are_never_shown(
    home: Path, notes: Path, run: Run
) -> None:
    import_list(home, f"{notes}/*.md")

    _, out = run("import")

    assert (raw(home) / "Notes" / "running-plan.md").exists()
    assert not (raw(home) / "Notes" / "contacts.md").exists()
    assert "contacts.md" in out
    assert "email" in out and "phone" in out
    assert "riya@example.com" not in out
    assert "98765 43210" not in out


def test_allow_brings_in_a_file_you_have_looked_at(home: Path, notes: Path, run: Run) -> None:
    import_list(home, f"{notes}/*.md")

    run("import", "--allow", str(notes / "contacts.md"))

    assert (raw(home) / "Notes" / "contacts.md").read_text() == CONTACTS


def test_sensitive_categories_are_left_out_by_name(home: Path, notes: Path, run: Run) -> None:
    (notes / "bank-statements.md").write_text("Nothing to see.\n")
    import_list(home, f"{notes}/**/*.md")

    _, out = run("import")

    assert not (raw(home) / "Notes" / "visa").exists()
    assert not (raw(home) / "Notes" / "bank-statements.md").exists()
    assert '"visa"' in out and '"bank"' in out


def test_the_rules_file_starts_with_the_defaults_and_your_changes_apply(
    home: Path, notes: Path, run: Run
) -> None:
    run("import")
    rules = (home / "import-rules.toml").read_text()
    assert '"visa"' in rules and "IN_AADHAAR" in rules and "min_score = 0.4" in rules

    (home / "import-rules.toml").write_text('[names]\nexcluded = ["running"]\n')
    import_list(home, f"{notes}/**/*.md")
    _, out = run("import")

    assert '"running"' in out
    assert not (raw(home) / "Notes" / "running-plan.md").exists()
    # Leaving "visa" off your list is your call: the file is imported.
    assert (raw(home) / "Notes" / "visa" / "renewal.md").exists()


def test_lines_starting_with_an_exclamation_mark_leave_files_out(
    home: Path, notes: Path, run: Run
) -> None:
    import_list(home, f"{notes}/**/*.md", f"!{notes}/running-plan.md")

    _, out = run("import")

    assert not raw(home).exists()
    assert "running-plan.md" not in out


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Aadhaar 2345 6789 0124 on file.", "identity number"),
        ("PAN ABCPE1234F for the return.", "identity number"),
        ("Card 4111 1111 1111 1111 expires soon.", "card number"),
        ("SSN 536-90-4399.", "identity number"),
    ],
)
def test_identity_and_card_numbers_are_found(
    home: Path, docs: Path, run: Run, text: str, kind: str
) -> None:
    (docs / "doc.md").write_text(text + "\n")
    import_list(home, f"{docs}/doc.md")

    _, out = run("import")

    assert kind in out
    assert not raw(home).exists()


def test_numbers_that_fail_their_checksum_are_not_findings(
    home: Path, docs: Path, run: Run
) -> None:
    text = "Order 2345 6789 0125, card 4111 1111 1111 1112, and the sample SSN 123-45-6789.\n"
    (docs / "doc.md").write_text(text)
    import_list(home, f"{docs}/doc.md")

    run("import")

    assert (raw(home) / "docs" / "doc.md").exists()


def test_keys_are_found_by_gitleaks(home: Path, docs: Path, run: Run) -> None:
    letters = string.ascii_letters + string.digits
    token = "ghp_" + "".join(secrets.choice(letters) for _ in range(36))
    (docs / "setup.md").write_text(f'github_token = "{token}"\n')
    import_list(home, f"{docs}/setup.md")

    _, out = run("import")

    assert "key (github-pat)" in out
    assert token not in out
    assert not raw(home).exists()


def test_without_gitleaks_nothing_is_imported(
    home: Path, notes: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "/nonexistent")
    import_list(home, f"{notes}/running-plan.md")

    code, out = run("import")

    assert code != 0
    assert "gitleaks" in out
    assert not raw(home).exists()


def test_your_identifiers_are_findings(home: Path, docs: Path, run: Run) -> None:
    (home / "identifiers.txt").write_text("# Words that identify you\nAsha Verma\n")
    (docs / "cv.md").write_text("Asha Verma, writer.\n")
    import_list(home, f"{docs}/cv.md")

    _, out = run("import")

    assert "your identifier" in out
    assert "Asha Verma" not in out
    assert not raw(home).exists()


def test_only_text_files_within_the_size_limit_are_imported(
    home: Path, docs: Path, run: Run
) -> None:
    (docs / "scan.pdf").write_bytes(b"%PDF-1.7\n")
    (docs / "big.txt").write_text("a" * 1_000_001)
    import_list(home, f"{docs}/*")

    _, out = run("import")

    assert not raw(home).exists()
    assert "scan.pdf" in out and "not a text file" in out
    assert "big.txt" in out and "over 1 MB" in out


def test_an_unchanged_file_is_not_imported_twice_and_a_changed_one_is_kept_beside_it(
    home: Path, notes: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    import_list(home, f"{notes}/running-plan.md")
    run("import")
    monkeypatch.setattr(importing, "now", lambda: datetime(2026, 11, 23, 8, 0))

    _, again = run("import")

    assert "Already imported" in again
    assert not (home / "memory" / "raw" / "imports" / "2026-11-23-0800").exists()

    (notes / "running-plan.md").write_text("Week 4: four runs.\n")
    run("import")

    first = raw(home) / "Notes" / "running-plan.md"
    second = home / "memory" / "raw" / "imports" / "2026-11-23-0800" / "Notes" / "running-plan.md"
    assert first.read_text() == "Week 3: three runs, long run Sunday.\n"
    assert second.read_text() == "Week 4: four runs.\n"


def test_an_import_waits_while_the_memory_is_being_changed(
    home: Path, notes: Path, run: Run
) -> None:
    import_list(home, f"{notes}/running-plan.md")
    memory = Memory.open(home / "memory")
    done = threading.Event()

    def work() -> None:
        run("import")
        done.set()

    with memory.lock:
        thread = threading.Thread(target=work)
        thread.start()
        assert not done.wait(0.5)
    thread.join(timeout=10)
    assert done.is_set()
    assert (raw(home) / "Notes" / "running-plan.md").exists()


# The pre-commit check


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "repo"
    folder.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=folder, check=True)
    monkeypatch.chdir(folder)
    return folder


def stage(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text)
    subprocess.run(["git", "add", name], cwd=repo, check=True)


def test_a_commit_that_mentions_an_identifier_is_stopped(home: Path, repo: Path, run: Run) -> None:
    (home / "identifiers.txt").write_text("asha.verma@example.com\n")
    stage(repo, "notes.py", "# fixture\nOWNER = 'asha.verma@example.com'\n")

    code, out = run("check-identifiers", "--staged")

    assert code == 1
    assert "notes.py:2" in out
    assert "asha.verma@example.com" not in out


def test_only_staged_lines_are_checked(home: Path, repo: Path, run: Run) -> None:
    (home / "identifiers.txt").write_text("Asha Verma\n")
    stage(repo, "notes.md", "Nothing personal.\n")
    (repo / "notes.md").write_text("Nothing personal.\nAsha Verma\n")

    code, _ = run("check-identifiers", "--staged")

    assert code == 0


def test_without_an_identifier_list_the_check_passes(home: Path, repo: Path, run: Run) -> None:
    stage(repo, "notes.md", "Asha Verma\n")

    code, _ = run("check-identifiers", "--staged")

    assert code == 0


NO_NETWORK = """
import socket, sys
attempts = []
# Connecting is what reaches out; making a socket alone does not (urllib3 binds one to
# ::1 on import to see whether IPv6 works).
def refuse(self, address):
    attempts.append(address)
    raise OSError("an import must not reach the network")
socket.socket.connect = refuse
socket.socket.connect_ex = refuse
import homunculus
try:
    homunculus.main(["import"])
except SystemExit:
    pass
print("network attempts:", len(attempts))
"""


def test_an_import_reaches_no_network(home: Path, notes: Path, tmp_path: Path) -> None:
    # In a process of its own, as the operator runs it, with no cached download to lean on.
    import_list(home, f"{notes}/contacts.md")
    env = {
        "PATH": __import__("os").environ["PATH"],
        "HOME": str(tmp_path),
        "HOMUNCULUS_HOME": str(home),
        "TLDEXTRACT_CACHE": str(tmp_path / "cache"),
    }
    result = subprocess.run(
        [sys.executable, "-c", NO_NETWORK],
        input="n\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    assert "email" in result.stdout, result.stderr
    assert "network attempts: 0" in result.stdout
