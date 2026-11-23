"""Prepare the pinned TypeScript parser resource at build time."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

VERSION = "6.0.3"
ARCHIVE_URL = f"https://registry.npmjs.org/typescript/-/typescript-{VERSION}.tgz"
ARCHIVE_SHA512 = base64.b64decode(
    "y2TvuxSZPDyQakkFRPZHKFm+KKVqIisdg9/CZwm9ftvKXLP8NRWj38/ODjNbr43SsoXqNuAisEf1GdCxqWcdBw=="
)
ARCHIVE_LIMIT = 5 * 1024 * 1024
VENDOR = Path("vendor/typescript")
FILES = {
    "lib/typescript.js": (9144216, "569177652966bd528c319171c7dd22860dbf72bde116cbc4f644f1d02bb12e39"),
    "package.json": (3527, "9332e97c30d3e53ed54910b89207ed657fb444066484df6e5b6965bf130865e9"),
    "LICENSE.txt": (9197, "a7d00bfd54525bc694b6e32f64c7ebcf5e6b7ae3657be5cc12767bce74654a47"),
    "ThirdPartyNoticeText.txt": (37824, "1af3c68039c57e539422da82a4faada506ce6d0ea6f90e0b699d02dbcdb7a90c"),
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def checked_files(root: Path) -> list[Path]:
    """Return verified paths from a prepared checkout or sdist."""
    bundle = root / VENDOR
    if not bundle.exists() and not bundle.is_symlink():
        return []
    if bundle.is_symlink() or not bundle.resolve().is_relative_to(root.resolve()):
        raise ValueError("unsafe TypeScript bundle directory")
    selected = []
    for name, (length, digest) in FILES.items():
        path = bundle / name
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_size != length
                or not path.resolve().is_relative_to(bundle.resolve())):
            raise ValueError(f"invalid TypeScript bundle file: {name}")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise ValueError(f"TypeScript bundle hash mismatch: {name}")
        selected.append(path)
    return selected


def prepare(root: Path, tarball: Path | None = None) -> None:
    if checked_files(root):
        return
    if tarball is None:
        opener = urllib.request.build_opener(NoRedirect)
        stream = opener.open(ARCHIVE_URL, timeout=30)
    else:
        stream = tarball.open("rb")
    with stream:
        data = stream.read(ARCHIVE_LIMIT + 1)
    if len(data) > ARCHIVE_LIMIT or hashlib.sha512(data).digest() != ARCHIVE_SHA512:
        raise ValueError("pinned TypeScript tarball SHA-512 mismatch")

    destination = root / VENDOR
    if not destination.parent.resolve().is_relative_to(root.resolve()):
        raise ValueError("unsafe TypeScript vendor directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as staging:
        staged_root = Path(staging)
        staged_bundle = staged_root / VENDOR
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for name, (length, _) in FILES.items():
                artifact = archive.getmember("package/" + name)
                if not artifact.isfile() or artifact.size != length:
                    raise ValueError(f"unexpected TypeScript tarball member: {name}")
                target = staged_bundle / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(artifact) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
        checked_files(staged_root)
        try:
            staged_bundle.rename(destination)
        except OSError:
            if not checked_files(root):
                raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tarball", type=Path, help="Already downloaded official npm archive for offline builds.")
    args = parser.parse_args()
    try:
        prepare(Path(__file__).resolve().parent, args.tarball)
    except (OSError, ValueError, KeyError, tarfile.TarError) as exc:
        print(f"TypeScript parser preparation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Prepared TypeScript {VERSION} parser and Apache-2.0 notices")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
