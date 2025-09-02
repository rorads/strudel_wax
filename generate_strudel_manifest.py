# /// script
# name = "generate-strudel-manifest"
# description = "Generate strudel.json manifest and archive previous versions"
# version = "0.1.0"
# authors = ["Rory Scott"]
# requires-python = ">=3.12"
# dependencies = []
# ///

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List


BASE_URL = "https://raw.githubusercontent.com/rorads/strudel_wax/main/"
MANIFEST_FILENAME = "strudel.json"
ARCHIVE_DIRNAME = ".archive-manifests"
HASH_FILENAME = ".manifest_hash"  # stored in project root (cwd)


def is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def load_gitignore_patterns(root: Path) -> List[str]:
    gi = root / ".gitignore"
    if not gi.exists():
        return []
    patterns: List[str] = []
    for line in gi.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def matches_patterns(rel_path: Path, patterns: List[str]) -> bool:
    """Very small subset of .gitignore-style matching using fnmatch semantics.
    This handles common glob patterns but does not fully implement .gitignore.
    """
    from fnmatch import fnmatch

    # Normalize to posix style for pattern checks
    s = rel_path.as_posix()

    for pat in patterns:
        negated = pat.startswith("!")
        p = pat[1:] if negated else pat

        # Directory pattern (e.g., foo/) => match anything under that directory
        if p.endswith("/"):
            p_match = p.rstrip("/")
            if s == p_match or s.startswith(p_match + "/"):
                return not negated
            continue

        # Leading slash patterns are anchored to repo root
        if p.startswith("/"):
            if fnmatch("/" + s, p):
                return not negated
            continue

        # Default: glob anywhere in the path
        if fnmatch(s, p) or any(fnmatch(part, p) for part in s.split("/")):
            return not negated

    return False


def iter_all_files(root: Path, ignore_patterns: List[str]) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)

        # Filter out hidden directories and those ignored by patterns
        pruned_dirs = []
        for d in list(dirnames):
            rel_sub = (rel_dir / d)
            if is_hidden(rel_sub):
                continue
            if matches_patterns(rel_sub, ignore_patterns):
                continue
            pruned_dirs.append(d)
        # In-place modify to control walk
        dirnames[:] = pruned_dirs

        for f in filenames:
            rel_file = rel_dir / f
            if is_hidden(rel_file):
                continue
            if matches_patterns(rel_file, ignore_patterns):
                continue
            yield rel_file


def compute_tree_hash(root: Path, ignore_patterns: List[str]) -> str:
    """Compute a hash over all file names (not contents), excluding hidden and ignored."""
    rel_paths = sorted(p.as_posix() for p in iter_all_files(root, ignore_patterns))
    hasher = hashlib.md5()
    for p in rel_paths:
        hasher.update(p.encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


def build_manifest(root: Path, base_url: str) -> OrderedDict:
    ignore_patterns = load_gitignore_patterns(root)

    # Collect .wav files per top-level directory
    per_dir: dict[str, List[str]] = {}

    for p in iter_all_files(root, ignore_patterns):
        if p.suffix.lower() != ".wav":
            continue
        # Only include files within a top-level directory (exclude files directly in root)
        parts = p.parts
        if len(parts) < 2:
            # Not in a subdirectory; skip to match archived structure
            continue
        top = parts[0]
        # Skip any hidden top-level dirs just in case
        if top.startswith("."):
            continue
        per_dir.setdefault(top, []).append(p.as_posix())

    # Sort directory names and file lists
    for k in per_dir:
        # Sort case-insensitively for human-friendly alphabetical order
        per_dir[k].sort(key=lambda s: s.casefold())

    ordered = OrderedDict()
    ordered["_base"] = base_url
    for dirname in sorted(per_dir.keys()):
        ordered[dirname] = per_dir[dirname]
    return ordered


def archive_existing_manifest(root: Path, manifest_path: Path) -> None:
    if not manifest_path.exists():
        return
    archive_dir = root / ARCHIVE_DIRNAME
    archive_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = archive_dir / f"strudel.{ts}.json"
    shutil.move(str(manifest_path), str(target))


def read_stored_hash(root: Path) -> str | None:
    p = root / HASH_FILENAME
    if not p.exists():
        return None
    try:
        return p.read_text().strip()
    except Exception:
        return None


def write_stored_hash(root: Path, value: str) -> None:
    p = root / HASH_FILENAME
    p.write_text(value + "\n")


def main(argv: List[str]) -> int:
    root = Path.cwd()

    # Always ignore the repo's own .git directory and archive area
    # by virtue of is_hidden(".git") and explicit patterns
    ignore_patterns = load_gitignore_patterns(root)

    current_hash = compute_tree_hash(root, ignore_patterns)
    stored_hash = read_stored_hash(root)

    manifest_path = root / MANIFEST_FILENAME
    should_generate = (stored_hash != current_hash) or (not (root / HASH_FILENAME).exists()) or (not manifest_path.exists())

    if not should_generate:
        print("Manifest up-to-date; no changes detected.")
        return 0

    # Archive existing manifest if present
    archive_existing_manifest(root, manifest_path)

    # Build new manifest
    manifest = build_manifest(root, BASE_URL)
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # Update stored hash
    write_stored_hash(root, current_hash)

    print(f"Wrote {MANIFEST_FILENAME} and updated {HASH_FILENAME} in project root.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
