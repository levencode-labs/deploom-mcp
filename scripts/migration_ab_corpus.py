"""Construct the immutable no-agent corpus and serve its registry on loopback."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import threading
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

from e2e_registry import PackageStore
from migration_ab_preflight import TARGET, prepare, project, write_json

REPO = Path(__file__).resolve().parents[1]
PUBLIC = "https://registry.npmjs.org"
REGISTRY = "http://127.0.0.1:4873"
TASKS = {
    "exact-update": "Update only kontur-fixture-a from 1.2.0 to exactly 1.9.0. Preserve b at 2.0.0 and c at 3.0.0.",
    "react-workspace": "Set root and packages/app React to 19.0.0, and packages/app react-dom to 19.0.0. Preserve legacy React and react-dom at 18.3.1, all other declarations and workspace links. Update only the two app SSR version literals as required and preserve its assertions.",
    "eslint-flat-config": "Upgrade ESLint from 8.57.1 to exactly 9.39.4 and migrate the legacy lint config to flat config. Preserve the no-unused-vars error rule, the lint command, the source, and is-number at 7.0.0. Remove the legacy config.",
}
FILES = {
    "exact-update": {"package.json", "package-lock.json"},
    "react-workspace": {"package.json", "package-lock.json", "packages/app/package.json", "packages/app/main.cjs"},
    "eslint-flat-config": {"package.json", "package-lock.json", ".eslintrc.json",
                           "eslint.config.cjs", "eslint.config.js", "eslint.config.mjs"},
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def files(root):
    found = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if "node_modules" in rel.parts:
            continue
        if path.is_symlink():
            raise ValueError("candidate contains a symlink")
        if path.is_file():
            found[rel.as_posix()] = path.read_bytes()
    return found


def hashes(root):
    return {name: digest(raw) for name, raw in files(root).items()}


def npm_env(cache):
    home = cache.parent
    for name in ("npm-user.empty", "npm-global.empty"):
        (home / name).touch(exist_ok=True)
    empty = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8", "HOME": str(cache.parent),
             "npm_config_cache": str(cache), "npm_config_registry": REGISTRY,
             "npm_config_audit": "false", "npm_config_fund": "false",
             "npm_config_ignore_scripts": "true", "npm_config_fetch_retries": "0",
             "npm_config_userconfig": str(home / "npm-user.empty"),
             "npm_config_globalconfig": str(home / "npm-global.empty"),
             "npm_config_update_notifier": "false"}
    return empty


def execute(args, cwd, env, timeout=90):
    return subprocess.run(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=timeout)


def npm(args, cwd, cache):
    return execute(["npm", *args], cwd, npm_env(cache))


def require(result, label):
    if result.returncode:
        # Never export untrusted subprocess output.
        raise RuntimeError(f"{label} exit={result.returncode}")


def url_path(url, origin):
    if not url.startswith(origin + "/"):
        raise ValueError("Unexpected registry origin")
    path = unquote(url[len(origin):])
    if any(p in (".", "..") for p in PurePosixPath(path).parts) or "?" in path or "#" in path:
        raise ValueError("Unexpected registry path")
    return path


def capture_registry(out, public, synthetic):
    entries = {}
    def add(path, body, kind):
        prior = entries.get(path)
        sha = digest(body)
        if prior and prior != [sha, kind]:
            raise ValueError("Conflicting registry capture")
        if not (out / sha).exists():
            (out / sha).write_bytes(body)
        entries[path] = [sha, kind]

    versions = {}
    for row in public:
        pkg = row["name"]
        versions.setdefault(pkg, {})[row["version"]] = row

    def capture(package):
        name, required = package
        with urlopen(PUBLIC + "/" + name.replace("/", "%2F"), timeout=25) as http:
            live = json.load(http)
        pinned = {}
        tarballs = []
        for version, row in required.items():
            info = deepcopy(live["versions"][version])
            dist = info["dist"]
            if (dist["integrity"], dist["tarball"]) != (row["integrity"], row["url"]):
                raise ValueError("Expected SRI/URL differs from npm")
            path = url_path(row["url"], PUBLIC)
            with urlopen(row["url"], timeout=25) as http:
                raw = http.read(32_000_000)
            got = "sha512-" + base64.b64encode(hashlib.sha512(raw).digest()).decode()
            if got != row["integrity"]:
                raise ValueError("Tarball SRI mismatch")
            dist["tarball"] = REGISTRY + path
            pinned[version] = info
            tarballs.append((path, raw))
        preferred = live["dist-tags"]["latest"]
        if preferred not in pinned:
            preferred = sorted(pinned, key=lambda x: tuple(int(y) for y in x.split("-")[0].split(".")))[-1]
        meta = {"name": name, "versions": pinned, "dist-tags": {"latest": preferred},
                "time": {v: live["time"][v] for v in pinned}}
        return name, meta, tarballs

    out.mkdir()
    with ThreadPoolExecutor(max_workers=10) as pool:
        for name, meta, tarballs in pool.map(capture, sorted(versions.items())):
            add("/" + name, json.dumps(meta, sort_keys=True).encode(), "application/json")
            for path, raw in tarballs:
                add(path, raw, "application/octet-stream")
    for name in synthetic.names():
        meta = synthetic.metadata(name)
        add("/" + name, json.dumps(meta, sort_keys=True).encode(), "application/json")
        for version in meta["versions"]:
            path = url_path(meta["versions"][version]["dist"]["tarball"], REGISTRY)
            add(path, synthetic.tarball(name, version)[0], "application/octet-stream")
    write_json(out / "index.json", entries)


@contextmanager
def frozen_registry(path):
    entries = json.loads((path / "index.json").read_bytes())
    for sha, _ in entries.values():
        if digest((path / sha).read_bytes()) != sha:
            raise ValueError("Frozen registry digest mismatch")
    hits = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            route = unquote(urlsplit(self.path).path)
            entry = entries.get(route)
            hits.append(route)
            if entry is None:
                self.send_error(404)
                return
            sha, mime = entry
            raw = (path / sha).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    with ThreadingHTTPServer(("127.0.0.1", 4873), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield hits
        finally:
            server.shutdown()
            thread.join()


def copy_clean(src, dst):
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("node_modules", "dist", ".git"))


def patch_lock(path):
    lockfile = path / "package-lock.json"
    lock = json.loads(lockfile.read_bytes())
    for row in lock["packages"].values():
        if row.get("resolved", "").startswith(PUBLIC + "/"):
            row["resolved"] = REGISTRY + url_path(row["resolved"], PUBLIC)
    write_json(lockfile, lock)


def read_public_rows(report, react):
    rows = list(react["artifacts"].values())
    for half in report["registryEntries"].values():
        for slot, row in half.items():
            rows.append({**row, "name": slot.rsplit("node_modules/", 1)[-1]})
    return rows


def copy_source_json(source, path):
    path.mkdir()
    for rel, text in source.items():
        full = path / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text, encoding="utf-8")


def prepare_corpus(stage, dest):
    # React upstream smoke emits independently verified baseline and candidate.
    require(execute([sys.executable, str(REPO / "scripts/smoke_react_workspace.py"),
                     "--out", str(stage / "react")], stage, dict(os.environ), timeout=480), "react smoke")
    (stage / "eslint-preflight").mkdir()
    prepare(stage / "eslint-preflight")
    eslint = stage / "eslint-preflight" / "eslint"
    report = json.loads((stage / "eslint-preflight/eslint-report.json").read_bytes())
    react = json.loads((REPO / "tests/fixtures/react_workspace.json").read_bytes())
    seed = json.loads((REPO / "scripts/e2e_seed.json").read_bytes())
    store = PackageStore(seed, REGISTRY)
    dest.mkdir()
    capture_registry(dest / "registry", read_public_rows(report, react), store)

    exact = dest / "exact-update"
    root = exact / "baseline"
    exact.mkdir()
    manifest = {"name": "exact-fixture", "version": "1.0.0", "private": True,
                "dependencies": {"kontur-fixture-a": "1.2.0", "kontur-fixture-b": "2.0.0", "kontur-fixture-c": "3.0.0"}}
    copy_source_json({"package.json": json.dumps(manifest), "test.cjs":
                      'const assert=require("node:assert/strict"); const p=require("./package.json");\n'
                      'for(const [name,version] of Object.entries(p.dependencies)) '
                      'assert.equal(require(name).version,version);\n'}, root)
    copy_clean(root, exact / "reference")
    manifest["dependencies"]["kontur-fixture-a"] = "1.9.0"
    write_json(exact / "reference/package.json", manifest)

    ws = dest / "react-workspace"
    ws.mkdir()
    copy_clean(stage / "react/injected-ui-baseline", ws / "baseline")
    copy_clean(stage / "react/trial", ws / "reference")

    lint = dest / "eslint-flat-config"
    lint.mkdir()
    copy_clean(eslint / "baseline", lint / "baseline")
    # The preparation step leaves the version-only negative control in place.
    from tempfile import TemporaryDirectory
    with TemporaryDirectory(dir=stage) as tmp:
        flat = Path(tmp) / "flat"
        project(flat, TARGET, True)
        copy_clean(flat, lint / "reference")
    shutil.copyfile(eslint / "reference/package-lock.json", lint / "reference/package-lock.json")
    for folder in (ws, lint):
        for side in ("baseline", "reference"):
            patch_lock(folder / side)

    manifest = {"registryIndexSha256": digest((dest / "registry/index.json").read_bytes()), "tasks": {}}
    with frozen_registry(dest / "registry"):
        for case in TASKS:
            folder = dest / case
            for side in ("baseline", "reference"):
                root = folder / side
                cache = stage / f"{case}-{side}-cache"
                if case == "exact-update":
                    require(npm(["install", "--package-lock-only"], root, cache), f"{case}/{side} lock")
                require(npm(["ci"], root, cache), f"{case}/{side} ci")
                require(check_runtime(case, root, cache), f"{case}/{side} behavior")
                for workspace in [root, *root.glob("packages/*")]:
                    installed = workspace / "node_modules"
                    if installed.is_dir():
                        shutil.rmtree(installed)
                if side == "baseline":
                    shutil.copytree(cache / "_cacache", folder / "warm/_cacache")
            regenerate = stage / (case + "-regenerate")
            copy_clean(folder / "reference", regenerate)
            shutil.copyfile(folder / "baseline/package-lock.json", regenerate / "package-lock.json")
            require(npm(["install", "--package-lock-only"], regenerate, stage / (case + "-recheck")), "frozen lock regeneration")
            regenerated = json.loads((regenerate / "package-lock.json").read_bytes())
            approved = json.loads((folder / "reference/package-lock.json").read_bytes())
            allowed = {(row.get("resolved"), row.get("integrity")) for row in approved["packages"].values()}
            for slot, row in regenerated["packages"].items():
                if row.get("integrity") and (row.get("resolved"), row["integrity"]) not in allowed:
                    raise RuntimeError(f"{case}: frozen lock selected an unapproved artifact at {slot}")
            write_json(folder / "reference/package-lock.json", regenerated)
            manifest["tasks"][case] = {side: hashes(folder / side) for side in ("baseline", "reference", "warm")}
    write_json(dest / "manifest.json", manifest)


def sandbox_node(root, argv):
    # All agent-authored code runs with a fresh PID and network namespace,
    # a clean environment, and a read-only view of the staged candidate.
    command = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session",
               "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin",
               "--ro-bind", "/lib", "/lib", "--ro-bind-try", "/lib64", "/lib64",
               "--ro-bind", "/opt/hostedtoolcache", "/opt/hostedtoolcache",
               "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
               "--ro-bind", str(root), "/work", "--chdir", "/work", "--clearenv",
               "--setenv", "PATH", os.environ["PATH"], "--setenv", "HOME", "/tmp", "--",
               "node", *argv]
    return execute(command, root, {"PATH": os.environ["PATH"]}, timeout=35)


def check_runtime(case, root, cache):
    if case == "exact-update":
        return sandbox_node(root, ["test.cjs"])
    if case == "react-workspace":
        listing = npm(["ls", "--all"], root, cache)
        if listing.returncode:
            return listing
        first = sandbox_node(root, ["packages/app/main.cjs"])
        return first if first.returncode else sandbox_node(root, ["packages/legacy/main.cjs"])
    return sandbox_node(root, ["node_modules/eslint/bin/eslint.js", "src.js"])


if __name__ == "__main__":
    stage, dest = map(Path, sys.argv[1:])
    prepare_corpus(stage.resolve(), dest.resolve())
