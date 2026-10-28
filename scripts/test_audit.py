"""Inventory the ten cleanup batches and compare full-suite coverage snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".test-audit"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def inventory() -> int:
    import pytest

    batches = json.loads((ROOT / "docs/test_batches.json").read_text(encoding="utf-8"))
    assigned = [file for batch in batches for file in batch["files"]]
    found = {p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").rglob("test_*.py")}
    duplicates = [file for file, count in Counter(assigned).items() if count > 1]
    if {b["batch"] for b in batches} != set(range(1, 11)) or len(batches) != 10 or duplicates or found != set(assigned):
        print({"duplicates": duplicates, "unassigned": sorted(found - set(assigned)),
               "absent": sorted(set(assigned) - found)})
        return 1
    owners = {file: batch["batch"] for batch in batches for file in batch["files"]}
    items = []

    class Collector:
        def pytest_collection_finish(self, session):
            for item in session.items:
                file = item.path.relative_to(ROOT).as_posix()
                doc = getattr(getattr(item, "obj", None), "__doc__", "") or ""
                items.append({
                    "id": item.nodeid, "batch": owners[file],
                    "scenario": re.findall(r"\b[A-Z]{2,3}-[A-Z-]+-\d{3}\b", doc),
                    "opt_in": sorted({m.name for m in item.iter_markers()} &
                                     {"live_provider", "live_telegram", "pii_model"}),
                })

    result = pytest.main(["--collect-only", "-q"], plugins=[Collector()])
    if result:
        return int(result)
    OUTPUT.mkdir(exist_ok=True)
    write_json(OUTPUT / "inventory.json", items)
    counts = Counter(item["batch"] for item in items)
    for batch in batches:
        print(f"{batch['batch']:2}: {len(batch['files']):2} files, {counts[batch['batch']]:4} cases: {batch['scope']}")
    return 0


def source_hashes() -> dict[str, str]:
    files = [p for folder in ("src", "examples") for p in (ROOT / folder).rglob("*.py")]
    files += [ROOT / "pyproject.toml", ROOT / "uv.lock"]
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(files)}


def snapshot(label: str) -> int:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", label):
        raise ValueError("Use a snapshot name containing letters, digits, underscores or hyphens")
    target = OUTPUT / label
    target.mkdir(parents=True, exist_ok=False)
    before = source_hashes()
    env = {**os.environ, "COVERAGE_FILE": str(target / ".coverage")}
    result = subprocess.run([
        sys.executable, "-m", "pytest", "-q", "-n", "4", "-rs",
        "--cov", "--cov-config=" + str(ROOT / "pyproject.toml"), "--cov-context=test",
        "--cov-report=json:" + str(target / "coverage.json"),
        "--cov-report=html:" + str(target / "html"),
        "--junitxml=" + str(target / "tests.xml"),
    ], cwd=ROOT, env=env, check=False)
    if result.returncode:
        return result.returncode
    if before != source_hashes():
        raise RuntimeError("Sources changed during measurement; snapshot is invalid")
    write_json(target / "metadata.json", {"source_hashes": before, "complete": True})
    return 0


def compare(before: Path, after: Path) -> int:
    metadata = [json.loads((p / "metadata.json").read_text(encoding="utf-8"))
                for p in (before, after)]
    if any(not m["complete"] for m in metadata) or metadata[0] != metadata[1]:
        raise ValueError("Compare successful full runs with identical sources and configuration")
    old, new = [json.loads((p / "coverage.json").read_text(encoding="utf-8"))["files"]
                for p in (before, after)]
    losses = {}
    contexts = None
    for name, data in old.items():
        current = new.get(name, {})
        lines = sorted(set(data["executed_lines"]) - set(current.get("executed_lines", [])))
        branches = sorted(set(map(tuple, data["executed_branches"])) -
                          set(map(tuple, current.get("executed_branches", []))))
        if lines or branches:
            line_contexts = {}
            if lines:
                from coverage import CoverageData

                if contexts is None:
                    contexts = CoverageData(basename=str(before / ".coverage"))
                    contexts.read()
                measured = {p.replace("\\", "/"): p for p in contexts.measured_files()}
                file = measured[name.replace("\\", "/")]
                recorded = contexts.contexts_by_lineno(file)
                line_contexts = {str(n): recorded.get(n, []) for n in lines}
            losses[name] = {
                "lines": lines, "branches": branches,
                "previous_line_contexts": line_contexts,
            }
    write_json(after / "losses.json", losses)
    print(f"{len(losses)} files lost coverage; details: {after / 'losses.json'}")
    return int(bool(losses))


def main() -> int:
    os.chdir(ROOT)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inventory")
    commands.add_parser("snapshot").add_argument("label")
    diff = commands.add_parser("compare")
    diff.add_argument("before", type=Path)
    diff.add_argument("after", type=Path)
    args = parser.parse_args()
    if args.command == "inventory":
        return inventory()
    if args.command == "snapshot":
        return snapshot(args.label)
    return compare(args.before, args.after)


if __name__ == "__main__":
    raise SystemExit(main())
