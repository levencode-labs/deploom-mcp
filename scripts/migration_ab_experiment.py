"""Execute one balanced pair through direct Responses with bounded repo tools."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
import time
from urllib.error import HTTPError
from urllib.request import build_opener, HTTPRedirectHandler, Request

from migration_ab_corpus import (FILES, REGISTRY, REPO, TASKS, check_runtime, copy_clean,
                                 digest, files, frozen_registry, hashes, npm, npm_env,
                                 require, sandbox_node)
from migration_ab_preflight import schedule, write_json

MODEL = "gpt-6-luna"
EFFORT = "low"
INPUT = 120_000
OUTPUT = 4000
STEPS = 26
SECONDS = 220
ESLINT_CONFIGS = {"eslint.config.cjs", "eslint.config.js", "eslint.config.mjs"}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


def read_api_key():
    value = os.environ.pop("OPENAI_API_KEY", "").lstrip("\ufeff").strip()
    if value.startswith("OPENAI_API_KEY="):
        value = value.removeprefix("OPENAI_API_KEY=").strip()
    if value[:1] in ("'", '"') and value[-1:] == value[:1]:
        value = value[1:-1]
    key = "".join(value.split())
    if not key:
        raise RuntimeError("Environment secret OPENAI_API_KEY is missing")
    if not re.fullmatch(r"sk-[A-Za-z0-9_-]{20,}", key):
        raise RuntimeError("Environment secret must contain a single OpenAI API key")
    return key


def api(key, suffix, payload, limit):
    request = Request("https://api.openai.com/v1/responses" + suffix, method="POST",
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      data=json.dumps(payload).encode())
    try:
        with build_opener(NoRedirect).open(request, timeout=min(30, limit)) as response:
            return json.load(response)
    except HTTPError as error:
        hint = []
        try:
            detail = json.loads(error.read(16_384)).get("error", {})
            for name in ("code", "param"):
                value = detail.get(name)
                if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,80}", value):
                    hint.append(f"{name}={value}")
        except (AttributeError, ValueError):
            pass
        raise RuntimeError(" ".join([f"OpenAI HTTP {error.code}", *hint])) from None
    except ValueError:
        raise RuntimeError("Invalid OpenAI HTTP transport value") from None


def schema(name, description, *keys):
    return {"type": "function", "name": name, "description": description, "strict": True,
            "parameters": {"type": "object", "properties": {k: {"type": "string"} for k in keys},
                           "required": list(keys), "additionalProperties": False}}


TOOLS = [
    schema("list_files", "List the current fixture files."),
    schema("read_file", "Read a source file listed in the project. For large files read a line window.", "path", "line"),
    schema("write_file", "Replace an approved file. Send its complete new text.", "path", "content"),
    schema("delete_file", "Remove the obsolete .eslintrc.json when migrating ESLint.", "path"),
    schema("resolve_lock", "Regenerate npm lockfile using the frozen registry after the manifest changes."),
    schema("check_project", "Run the original project checks after installing from the frozen lock."),
    schema("read_reference", "Read shared evidence for the exact migration task."),
]
MCP = schema("deploom_plan", "Ask the packaged DepLoom MCP for the bounded migration plan and current capabilities.")
SKILL = schema("read_skill", "Read pinned DepLoom skill SKILL.md or a bundled references/*.md file.", "path")


def json_file(path):
    return json.loads(path.read_bytes())


def valid_lock(root, registry_index):
    lock = json_file(root / "package-lock.json")
    assert lock["lockfileVersion"] == 3
    for slot, row in lock["packages"].items():
        if "node_modules/" not in slot or row.get("link"):
            continue
        relative = row["resolved"].removeprefix(REGISTRY)
        if row["resolved"] != REGISTRY + relative or not relative.startswith("/"):
            raise ValueError("non-frozen resolution")
        if relative not in registry_index or registry_index[relative][1] != "application/octet-stream":
            raise ValueError("non-frozen artifact")


def oracle(case, baseline, root, reference, cache, registry_index):
    baseline_files, observed, expected = (files(p) for p in (baseline, root, reference))
    changed = {p for p in baseline_files.keys() | observed.keys()
               if baseline_files.get(p) != observed.get(p)}
    if not changed <= FILES[case]:
        return "OUTSIDE_SCOPE"
    if observed.keys() - ESLINT_CONFIGS != expected.keys() - ESLINT_CONFIGS:
        return "FILE_SET_MISMATCH"
    for path in expected.keys() - (ESLINT_CONFIGS | {"package-lock.json"}):
        if path.endswith("package.json"):
            if json.loads(observed[path]) != json.loads(expected[path]):
                return "DECLARATION_OR_CHECK_DRIFT"
        elif observed[path] != expected[path]:
            if (case == "react-workspace" and path == "packages/app/main.cjs"
                    and observed[path] == expected[path].removesuffix(b"\n")):
                continue
            return "SOURCE_OR_CHECK_DRIFT"
    if json.loads(observed["package-lock.json"]) != json.loads(expected["package-lock.json"]):
        return "LOCK_OR_HELD_PLACEMENT_MISMATCH"
    valid_lock(root, registry_index)
    if npm(["ci"], root, cache).returncode:
        return "FROZEN_INSTALL_FAILED"
    if check_runtime(case, root, cache).returncode:
        return "RUNTIME_FAILED"
    if case == "eslint-flat-config":
        if len(observed.keys() & ESLINT_CONFIGS) != 1 or ".eslintrc.json" in observed:
            return "FLAT_CONFIG_REQUIRED"
        path = root / "src.js"
        original = path.read_bytes()
        try:
            name = "unused_" + secrets.token_hex(8)
            path.write_bytes(original + f"\nconst {name} = 1;\n".encode())
            result = sandbox_node(root, ["node_modules/eslint/bin/eslint.js", "--format", "json", "src.js"])
            if result.returncode != 1 or [m["ruleId"] for row in json.loads(result.stdout)
                                         for m in row["messages"]] != ["no-unused-vars"]:
                return "LINT_RULE_CHANGED"
        finally:
            path.write_bytes(original)
    return "PASS"


def plan_arguments(case, root):
    if case == "react-workspace":
        from deploom_mcp.workspace_inventory import observe_npm_workspace

        targets = [{"selector": {"owner": owner, "section": "dependencies", "name": name},
                    "approvedLines": [19], "versions": ["19.0.0"], "exactTarget": "19.0.0"}
                   for owner, name in [(".", "react"), ("packages/app", "react"), ("packages/app", "react-dom")]]
        packuments = json_file(REPO / "tests/fixtures/react_workspace.json")["packuments"]
        return {"project_path": str(root), "workspace_request": {
            "schemaVersion": 2, "goalKind": "TARGETED_UPGRADE", "targets": targets,
            "inventoryDigest": observe_npm_workspace(root)["inventoryDigest"],
            "environment": {"node": "24.18.0", "npm": "11.16.0"}, "topology": None,
            "packuments": packuments}}
    name, target = ("eslint", "9.39.4") if case == "eslint-flat-config" else ("kontur-fixture-a", "1.9.0")
    return {"project_path": str(root), "registry": REGISTRY, "use_system_proxy": True,
            "target_level": "yellow", "deadline_seconds": 14, "target_mode": "pinned",
            "objective": "explicit-version-targets",
            "mutable_packages": [name], "exact_targets": {name: target}}


async def mcp_call(case, root, workspace):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = npm_env(workspace / "npm-cache")
    env.update(DEPLOOM_MCP_STATE_DIR=str(workspace / "mcp-state"),
               DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE="1", DEPLOOM_MCP_MIGRATION_OFFLINE="1",
               DEPLOOM_MCP_ALLOWED_PROJECT_REGISTRY=REGISTRY, XDG_CACHE_HOME=str(workspace / "mcp-cache"),
               HTTP_PROXY="http://127.0.0.1:1", HTTPS_PROXY="http://127.0.0.1:1",
               ALL_PROXY="http://127.0.0.1:1", NO_PROXY="127.0.0.1,localhost")
    params = StdioServerParameters(command=sys.executable, args=["-m", "deploom_mcp"],
                                   cwd=str(workspace), env=env)
    with open(os.devnull, "w") as discard:
        async with stdio_client(params, errlog=discard) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                tools = await client.list_tools()
                assert "get_upgrade_plan" in {t.name for t in tools.tools}
                response = await client.call_tool("get_upgrade_plan", plan_arguments(case, root))
                value = getattr(response, "structuredContent", None)
                if value is None and response.content:
                    value = json.loads(response.content[0].text)
                if case == "react-workspace" and value.get("ok"):
                    return {k: value[k] for k in ("ok", "jointAssignment", "constraintModel", "action")}
                if value.get("ok") and len(json.dumps(value)) > 26000:
                    return {k: value[k] for k in ("ok", "summary", "decisionCompleteness", "action") if k in value}
                return value


def check_actions(case, root, baseline, reference, registry_index, cache, selected, args, workspace, skill):
    path = args.get("path")
    if selected == "list_files":
        return sorted(files(root))
    if selected == "read_file":
        snapshots = files(root)
        if path not in snapshots:
            return {"error": "not a project file"}
        start = max(0, int(args["line"]) - 1)
        lines = snapshots[path].decode("utf-8").splitlines()
        return {"start": start + 1, "totalLines": len(lines), "lines": lines[start:start + 95]}
    if selected == "write_file" and path in FILES[case] and path != "package-lock.json":
        raw = args["content"].encode("utf-8")
        if len(raw) > 20_000:
            return {"error": "file exceeds allowed byte count"}
        if path.endswith("package.json") and json.loads(raw) != json_file(reference / path):
            return {"error": "manifest must preserve frozen fields and match authorized exact changes"}
        (root / path).write_bytes(raw)
        return {"written": path}
    if selected == "delete_file" and case == "eslint-flat-config" and path == ".eslintrc.json":
        (root / path).unlink(missing_ok=True)
        return {"removed": path}
    if selected in ("resolve_lock", "check_project"):
        valid_lock(root, registry_index)
        if selected == "resolve_lock" and case == "react-workspace":
            lock = root / "package-lock.json"
            saved = lock.read_bytes()
            lock.unlink()
            try:
                result = npm(["install", "--package-lock-only"], root, cache)
            finally:
                if not lock.exists():
                    lock.write_bytes(saved)
            if result.returncode:
                lock.write_bytes(saved)
        else:
            result = npm(["install", "--package-lock-only"], root, cache) if selected == "resolve_lock" else npm(["ci"], root, cache)
        if result.returncode:
            return {"exit": result.returncode, "diagnostic": result.stderr[-900:]}
        if selected == "check_project":
            result = check_runtime(case, root, cache)
            return {"exit": result.returncode, "diagnostic": result.stderr[-900:]}
        return {"exit": 0}
    if selected == "read_reference":
        return {
            "exact-update": "Use exact dependencies in package.json and a lock-only npm resolution. Keep the held direct dependencies unchanged.",
            "react-workspace": "npm workspace owners are independent declarations. Keep packages/legacy pinned; app React and react-dom must resolve the same major. Authorized SSR edits are version-literal replacements only.",
            "eslint-flat-config": "In ESLint 9 configurations export a flat config array from eslint.config.cjs or .mjs. Configure the same rule severity under rules. Flat config uses languageOptions.sourceType for CommonJS.",
        }[case]
    if skill and selected == "read_skill":
        if not re.fullmatch(r"SKILL\.md|references/[a-z0-9-]+\.md", path):
            return {"error": "unknown skill path"}
        file = skill / path
        return file.read_text(encoding="utf-8") if file.is_file() else {"error": "unknown skill path"}
    if skill and selected == "deploom_plan":
        return asyncio.run(asyncio.wait_for(mcp_call(case, root, workspace), timeout=30))
    return {"error": "tool or path unavailable"}


def arm(key, corpus, pair, side, home, registry_index, hits, out):
    from deploom_mcp.distribution import skill_root
    from skill_integrity import verify_contract

    case = pair["task"]
    folder = corpus / case
    baseline, reference = folder / "baseline", folder / "reference"
    home.mkdir()
    root = home / "project"
    copy_clean(baseline, root)
    cache = home / "npm-cache"
    if pair["cache"] == "warm":
        shutil.copytree(folder / "warm", cache)
    else:
        cache.mkdir()
    tools = TOOLS.copy()
    skill = None
    setup_started = time.monotonic()
    if side == "B":
        skill = skill_root()
        assert not verify_contract(skill)
        tools += [MCP, SKILL]
    instructions = ("You work on a synthetic dependency migration fixture. Use only the provided tools. "
                    "Keep project behavior and every held declaration. Do not edit test scripts, skip checks, "
                    "or add dependencies outside the task. Use the fixed registry via resolve_lock. "
                    "Check the project before finishing. You can read the shared migration reference.")
    if skill:
        instructions += " DepLoom MCP and its pinned skill are available. Read SKILL.md and use them as relevant."
    data = [{"role": "user", "content": TASKS[case]}]
    row = {"pair": pair["id"], "arm": side, "model": MODEL,
           "skillSetupSeconds": time.monotonic() - setup_started,
           "inputTokens": 0, "outputTokens": 0, "cachedTokens": 0, "reasoningTokens": 0,
           "requests": 0, "responses": 0, "toolCalls": [], "registryRequests": 0,
           "terminal": "STEP_LIMIT", "verification": "NOT_RUN"}
    deadline = time.monotonic() + SECONDS
    started, hit_start = time.monotonic(), len(hits)
    try:
        for _ in range(STEPS):
            remaining = deadline - time.monotonic()
            if remaining < 2:
                row["terminal"] = "TIME_LIMIT"
                break
            base = {"model": MODEL, "input": data, "instructions": instructions, "tools": tools,
                    "reasoning": {"effort": EFFORT}, "parallel_tool_calls": False}
            count = {k: v for k, v in base.items() if k != "parallel_tool_calls"}
            n = api(key, "/input_tokens", count, remaining)["input_tokens"]
            available = OUTPUT - row["outputTokens"]
            if row["inputTokens"] + n > INPUT or available < 32:
                row["terminal"] = "TOKEN_LIMIT"
                break
            row["requests"] += 1
            response = api(key, "", {**base, "store": False, "max_output_tokens": min(700, available),
                                     "include": ["reasoning.encrypted_content"]},
                           max(1, deadline - time.monotonic()))
            row["responses"] += 1
            usage = response["usage"]
            row["inputTokens"] += usage["input_tokens"]
            row["outputTokens"] += usage["output_tokens"]
            row["cachedTokens"] += (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
            row["reasoningTokens"] += (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0)
            row["reportedModel"] = response["model"]
            if row["inputTokens"] > INPUT or row["outputTokens"] > OUTPUT:
                row["terminal"] = "TOKEN_LIMIT"
                break
            if response["status"] != "completed":
                row["terminal"] = "RESPONSE_" + response["status"].upper()
                break
            calls = [it for it in response["output"] if it["type"] == "function_call"]
            if not calls:
                row["terminal"] = "FINISHED"
                break
            assert len(calls) == 1
            call = calls[0]
            data.extend(response["output"])
            t = time.monotonic()
            try:
                result = check_actions(case, root, baseline, reference, registry_index, cache, call["name"],
                                       json.loads(call["arguments"]), home, skill)
                value = json.dumps(result, ensure_ascii=True)
                if len(value) > 28_000:
                    value = json.dumps({"error": "tool output exceeded prescribed limit"})
            except (ValueError, RuntimeError, OSError, TimeoutError, AssertionError):
                value = '{"error":"bounded tool operation failed"}'
            row["toolCalls"].append({"name": call["name"], "seconds": round(time.monotonic() - t, 3)})
            data.append({"type": "function_call_output", "call_id": call["call_id"], "output": value})
    except Exception as error:
        if re.match(r"OpenAI HTTP [0-9]{3}", str(error)):
            row["terminal"] = str(error)[:15]
            row["apiError"] = str(error)
        else:
            row["terminal"] = type(error).__name__
    row["agentSeconds"] = round(time.monotonic() - started, 3)
    row["registryRequests"] = len(hits) - hit_start
    verify_started = time.monotonic()
    candidate = home / "candidate-verification"
    try:
        files(root)  # reject a symlink before staging anything the agent wrote
        copy_clean(root, candidate)
        row["verification"] = oracle(case, baseline, candidate, reference, home / "oracle-cache", registry_index)
    except (RuntimeError, ValueError, OSError, AssertionError):
        row["verification"] = "ORACLE_FAILED"
    row["oracleSeconds"] = round(time.monotonic() - verify_started, 3)
    row["acceptedWithinBudget"] = (row["verification"] == "PASS" and row["terminal"] == "FINISHED"
                                  and row["agentSeconds"] <= SECONDS)
    row["candidateSha256"] = digest(json.dumps(hashes(root), sort_keys=True).encode())
    write_json(out, row)
    return row


def preflight(corpus, evidence):
    manifest = json_file(corpus / "manifest.json")
    registry = json_file(corpus / "registry/index.json")
    assert digest((corpus / "registry/index.json").read_bytes()) == manifest["registryIndexSha256"]
    for case, sides in manifest["tasks"].items():
        for side, expected in sides.items():
            assert hashes(corpus / case / side) == expected
        source = corpus / case / "baseline"
        reference = corpus / case / "reference"
        assert oracle(case, source, source, reference, evidence / "unused", registry) != "PASS"
        temp = evidence / ("curator-" + case)
        copy_clean(reference, temp)
        oracle_cache = evidence / "oracle-cache"
        observed = oracle(case, source, temp, reference, oracle_cache, registry)
        if observed != "PASS":
            if observed == "RUNTIME_FAILED":
                runtime = check_runtime(case, temp, evidence / "oracle-cache")
                print(runtime.stderr[-2200:], flush=True)
            delta = sorted(p for p in files(source).keys() | files(temp).keys()
                           if files(source).get(p) != files(temp).get(p))
            raise RuntimeError(f"{case}: positive oracle returned {observed}, changed files {delta}")
        if case == "react-workspace":
            app = temp / "packages/app/main.cjs"
            original = app.read_bytes()
            assert original.endswith(b"\n")
            without_lf = original.removesuffix(b"\n")
            for label, content, expected in (
                ("missing final LF", without_lf, "PASS"),
                ("extra final LF", original + b"\n", "SOURCE_OR_CHECK_DRIFT"),
                ("changed assertion", without_lf.replace(b"React, ui.react", b"React, React", 1),
                 "SOURCE_OR_CHECK_DRIFT"),
            ):
                app.write_bytes(content)
                actual = oracle(case, source, temp, reference, oracle_cache, registry)
                assert actual == expected, f"{label}: expected {expected}, got {actual}"
            app.write_bytes(original)
        (temp / "unsupported.txt").write_text("outside scope")
        assert oracle(case, source, temp, reference, evidence / "unused", registry) == "OUTSIDE_SCOPE"
        (temp / "unsupported.txt").unlink()
        doc = json_file(temp / "package.json")
        doc["scripts"] = {"test": "true"}
        write_json(temp / "package.json", doc)
        assert oracle(case, source, temp, reference, evidence / "unused", registry) in (
            "DECLARATION_OR_CHECK_DRIFT", "OUTSIDE_SCOPE")
    from deploom_mcp.distribution import skill_root
    from skill_integrity import verify_contract

    assert not verify_contract(skill_root())
    for case in manifest["tasks"]:
        home = evidence / ("deploom-" + case)
        working = home / "project"
        copy_clean(corpus / case / "baseline", working)
        result = asyncio.run(asyncio.wait_for(mcp_call(case, working, home), timeout=45))
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError(f"{case}: pinned MCP did not return a plan")
        print(f"{case}: pinned MCP returned a plan", flush=True)
    return manifest


def api_smoke(key):
    probe = schema("probe", "Run the required fixture probe.")
    data = [{"role": "user", "content": "Call the probe tool, then say OK after receiving its output."}]
    base = {"model": MODEL, "input": data, "reasoning": {"effort": EFFORT}, "tools": [probe]}
    assert api(key, "/input_tokens", base, 30)["input_tokens"] > 0
    response_options = {"store": False, "parallel_tool_calls": False, "max_output_tokens": 200,
                        "include": ["reasoning.encrypted_content"]}
    initial = api(key, "", {**base, **response_options, "tool_choice": "required"}, 30)
    calls = [item for item in initial["output"] if item["type"] == "function_call"]
    assert initial["status"] == "completed" and len(calls) == 1 and calls[0]["name"] == "probe"
    data.extend(initial["output"])
    data.append({"type": "function_call_output", "call_id": calls[0]["call_id"], "output": "OK"})
    assert api(key, "/input_tokens", base, 30)["input_tokens"] > 0
    final = api(key, "", {**base, **response_options, "tool_choice": "none"}, 30)
    assert final["status"] == "completed" and final["usage"]["input_tokens"] > 0
    print("Responses API tool continuation passed for " + final["model"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--pair")
    parser.add_argument("--api-smoke", action="store_true")
    args = parser.parse_args()
    if args.api_smoke:
        api_smoke(read_api_key())
        return
    if not args.corpus or not args.out:
        parser.error("--corpus and --out are required")
    out = args.out.resolve()
    out.mkdir()
    corpus = args.corpus.resolve()
    corpus_hash = digest((corpus / "manifest.json").read_bytes())
    pair = next((p for p in schedule()["plannedPairs"] if p["id"] == args.pair), None)
    with frozen_registry(corpus / "registry") as hits:
        if args.pair is None:
            preflight(corpus, out)
            write_json(out / "preflight.json", {"status": "PASS", "modelSessions": 0, "corpusSha256": corpus_hash})
            return
        if not pair:
            raise RuntimeError("Unknown pair id")
        key = read_api_key()
        registry_index = json_file(corpus / "registry/index.json")
        rows = []
        for side in pair["order"]:
            rows.append(arm(key, corpus, pair, side, out / ("arm-" + side), registry_index, hits,
                            out / f"{side}.json"))
        write_json(out / "pair.json", {**pair, "corpusSha256": corpus_hash, "runs": rows})
        print(f"{pair['id']}: " + " ".join(f"{r['arm']}={r['verification']}" for r in rows))


if __name__ == "__main__":
    main()
