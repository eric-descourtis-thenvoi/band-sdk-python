"""Verify imported source provenance and the SDK's published file digests."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import re
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

logger = logging.getLogger(__name__)


def source_hashes(repository: Path, revision: str) -> dict[str, str]:
    archive = subprocess.run(
        ["git", "-C", str(repository), "archive", revision],
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as files:
        digests = {}
        for member in files.getmembers():
            if member.isfile():
                stream = files.extractfile(member)
                if stream is None:
                    raise ValueError("Missing source archive member: " + member.name)
                with stream:
                    digests[member.name] = hashlib.sha256(stream.read()).hexdigest()
        return digests


def safe_target(repository: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or "\\" in name:
        raise ValueError("Invalid publication target: " + name)
    target = repository / relative
    if target.is_symlink() or not target.resolve().is_relative_to(repository.resolve()):
        raise ValueError("Publication target escapes the repository: " + name)
    return target


def verify(contracts: Path, source_repository: Path | None = None) -> int:
    manifest = json.loads((contracts / "SOURCE.json").read_text(encoding="utf-8"))
    revision = manifest["revision"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Source revision must be an immutable full commit ID")
    originals = (
        source_hashes(source_repository, revision)
        if source_repository is not None
        else None
    )
    targets: set[str] = set()
    for name, entry in manifest["original_files"].items():
        target_name = entry["target"]
        if target_name in targets:
            raise ValueError("Duplicate publication target: " + target_name)
        targets.add(target_name)
        target = safe_target(contracts.parent, target_name)
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != entry["published_sha256"]:
            raise ValueError("Published byte digest mismatch: " + name)
        if originals is not None and originals.get(name) != entry["sha256"]:
            raise ValueError("Upstream source digest mismatch: " + name)
    return len(targets)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--contracts", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--source-repo", type=Path)
    args = parser.parse_args()
    count = verify(args.contracts, args.source_repo)
    logger.info("Verified %s mapped publication targets", count)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
