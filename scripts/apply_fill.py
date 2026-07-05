"""Fill a job application form from a Homunculus application plan.

Runs on the HOST (never in Docker) so the browser is visible: you watch
the fill, review every field, and click submit yourself — the script
never submits. The agent's role ended when it wrote the plan; from here
everything is deterministic Playwright.

Usage:
    uv sync --group apply                                # once
    uv run --group apply playwright install chromium     # once
    uv run --group apply python scripts/apply_fill.py <application-id> [--headless]

The plan lives in workspace/applications/<id>.json (written by the
agent's prepare_application/draft_answer tools). Container paths in the
plan (/app/career/...) are mapped back to the host career repo.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLANS_DIR = REPO_ROOT / "workspace" / "applications"
CAREER_HOST = Path(
    os.environ.get("HOMUNCULUS_CAREER_DIR_HOST", REPO_ROOT / "../../career-ops")
).resolve()


def host_path(value: str) -> str:
    """Map a container path from the plan back to the host filesystem."""
    if value.startswith("/app/career/"):
        return str(CAREER_HOST / value.removeprefix("/app/career/"))
    return value


def fill(plan: dict, headless: bool) -> None:
    from playwright.sync_api import sync_playwright

    filled: list[str] = []
    skipped: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1280, "height": 1400})
        page.goto(plan["url"], wait_until="domcontentloaded")
        page.wait_for_timeout(2500)  # ATS forms hydrate after load

        for field in plan["fields"]:
            label, value, ftype = field["label"], field["value"], field["type"]
            if value in (None, ""):
                skipped.append(f"{label} (no value)")
                continue
            try:
                if ftype == "input_file":
                    page.locator("input[type=file]").first.set_input_files(
                        host_path(value), timeout=8000
                    )
                elif ftype in ("input_text", "textarea"):
                    loc = page.get_by_label(label, exact=False).first
                    loc.fill(str(value), timeout=8000)
                elif field.get("options"):
                    # A pre-validated choice (draft_answer only accepts
                    # exact option labels). Greenhouse renders these as
                    # comboboxes: open, type the option, pick it. The
                    # human still reviews the result in the open browser.
                    loc = page.get_by_label(label, exact=False).first
                    loc.click(timeout=8000)
                    loc.fill(str(value), timeout=8000)
                    page.get_by_role("option", name=str(value), exact=True).first.click(
                        timeout=5000
                    )
                else:
                    skipped.append(f"{label} ({ftype} — choose manually)")
                    continue
                filled.append(label)
            except Exception as e:  # noqa: BLE001 — report and move on, never die mid-form
                skipped.append(f"{label} (fill failed: {type(e).__name__})")

        print(f"\n✓ filled {len(filled)}/{len(plan['fields'])} fields:")
        for f in filled:
            print(f"    • {f}")
        if skipped:
            print("  needs your hand:")
            for s in skipped:
                print(f"    • {s}")

        if headless:
            page.screenshot(path=str(PLANS_DIR / f"{plan['id']}.png"), full_page=True)
            print(f"\n[headless] screenshot: workspace/applications/{plan['id']}.png")
            browser.close()
        else:
            # Wait on the WINDOW, not stdin — the script also runs
            # detached (agent-launched), where input() would EOF and
            # kill the browser out from under the reviewer.
            print("\nReview the form, fill the dropdowns, submit if you want — then close the browser window.")
            try:
                page.wait_for_event("close", timeout=0)
            except Exception:  # noqa: BLE001 — browser gone = review over
                pass
            browser.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("application_id")
    ap.add_argument("--headless", action="store_true", help="verification mode: fill, screenshot, never shown")
    args = ap.parse_args()

    plan_path = PLANS_DIR / f"{args.application_id}.json"
    if not plan_path.is_file():
        known = ", ".join(p.stem for p in PLANS_DIR.glob("*.json")) or "none"
        sys.exit(f"no plan '{args.application_id}' (known: {known})")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    print(f"── {plan['title']} · {plan['url']}")
    unanswered = [f["label"] for f in plan["fields"] if f["required"] and not f["value"]]
    if unanswered:
        print("⚠ required fields with no value (will be skipped):")
        for u in unanswered:
            print(f"    • {u}")
    fill(plan, headless=args.headless)


if __name__ == "__main__":
    main()
