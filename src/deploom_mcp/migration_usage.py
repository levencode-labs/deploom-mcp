"""Local syntax observations only. No source bodies leave the trusted host."""
from __future__ import annotations
import json,os,shutil,stat,time,uuid
from contextlib import ExitStack
from itertools import groupby
from pathlib import Path
from deploom_mcp.migration_sources import digest,encoded
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.workspace_input_io import bound_directory, selected_root
from deploom_mcp.workspace_inventory import _read_bytes, _safe
from deploom_mcp.migration_evidence import _app
from deploom_mcp.goal_report import _plain_under
from deploom_mcp.process_runner import run_process
SUFFIXES={'.js','.jsx','.mjs','.cjs','.ts','.tsx','.mts','.cts'}
EXCLUDED={'node_modules','.git','.dependency-roadmap','.idea','.vs','.vscode','.fleet','.claude','.codex'}
_BUNDLED_PARSER=Path(__file__).with_name('resources')/'typescript'/'lib'/'typescript.js'
_BUNDLED_PARSER_SHA256='569177652966bd528c319171c7dd22860dbf72bde116cbc4f644f1d02bb12e39'
_PARSER_VERSION='6.0.3'

def inspect_usage(project,packages,state,*,deadline,source_identity,config_keys=None):
    result=_app(reason='LOCAL_INSPECTION_UNAVAILABLE');project=Path(project);state=Path(state)
    try:
        project = _plain_under(project.absolute(), project.absolute())
        state = _plain_under(state.absolute(), state.absolute())
        project,state=project.resolve(),state.resolve()
        if state.is_relative_to(project) or project.is_relative_to(state):
            return _app(reason='USAGE_STATE_OVERLAP_REJECTED')
    except (OSError, GoalContractError):
        return _app(reason='USAGE_ROOT_UNAVAILABLE')
    parser_value=os.environ.get('DEPLOOM_MCP_TYPESCRIPT_PATH')
    expected=os.environ.get('DEPLOOM_MCP_TYPESCRIPT_SHA256')
    if parser_value is None and expected is None:
        if not _BUNDLED_PARSER.is_file():return result
        parser,expected=_BUNDLED_PARSER,_BUNDLED_PARSER_SHA256
    else:
        if not parser_value or expected is None:return result
        parser=Path(parser_value)
    if not parser.is_absolute() or len(expected)!=64:return result
    try:
        if parser.resolve().is_relative_to(project) or parser.is_symlink() or not parser.is_file() or parser.stat().st_size>16*1024*1024 or digest(parser.read_bytes())!=expected:
            return _app(reason='HOST_PARSER_IDENTITY_REJECTED')
        package=json.loads((parser.parent.parent/'package.json').read_bytes())
        if package.get('name')!='typescript' or package.get('version')!=_PARSER_VERSION:return _app(reason='HOST_PARSER_VERSION_UNSUPPORTED')
    except (OSError,ValueError,AttributeError,RuntimeError):return _app(reason='HOST_PARSER_IDENTITY_UNAVAILABLE')
    try:
        with selected_root(project, _safe(project, directory=True)) as root_handle:
            return _inspect(project, packages, state, parser, expected, deadline, source_identity, config_keys, root_handle)
    except (OSError, GoalContractError):
        return _app(reason='USAGE_ROOT_UNAVAILABLE')


def _inspect(project, packages, state, parser, expected, deadline, source_identity, config_keys, root_handle):
    result=_app(reason='LOCAL_INSPECTION_UNAVAILABLE')
    files=[];identities=[];total=0;coverage={'eligible':0,'scanned':0,'excludedDirectories':0,'unreadable':0,'unsupported':0,'limitReached':False}
    def traversal_error(_error):
        coverage['unreadable']+=1
    for directory,dirs,names in os.walk(project,followlinks=False,onerror=traversal_error):
        if time.monotonic()>=deadline:return _app(reason='USAGE_DEADLINE')
        parent=Path(directory)
        kept=[]
        for name in sorted(dirs):
            if name in EXCLUDED:
                coverage['excludedDirectories']+=1
                continue
            try:
                info=(parent/name).lstat()
            except OSError:
                coverage['unreadable']+=1
                continue
            if (not stat.S_ISDIR(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400):
                coverage['unsupported']+=1
            else:
                kept.append(name)
        dirs[:]=kept
        with ExitStack() as handles:
            binding = None
            for name in sorted(names):
                path = parent / name
                suffix = path.suffix.lower()
                if suffix in {'.vue','.svelte','.astro'}:
                    coverage['unsupported'] += 1
                    continue
                keys = {package: selectors for pattern, package, selectors in config_keys or ()
                        if pattern.fullmatch(name)}
                if suffix not in SUFFIXES and not keys:
                    continue
                coverage['eligible'] += 1
                if len(files) >= 256:
                    coverage['limitReached'] = True
                    continue
                if time.monotonic() >= deadline:
                    return _app(reason='USAGE_DEADLINE')
                try:
                    if binding is None:
                        binding = handles.enter_context(bound_directory(project, parent, root_handle))
                    raw = _read_bytes(path, 131072, parent_binding=binding)
                    if total + len(raw) > 1048576:
                        coverage['limitReached'] = True
                        continue
                    source = raw.decode('utf-8-sig')
                    relative = path.relative_to(project).as_posix()
                except GoalContractError as error:
                    if error.code == 'WORKSPACE_INPUT_LIMIT':
                        coverage['limitReached'] = True
                    else:
                        coverage['unreadable'] += 1
                    continue
                except (OSError, ValueError):
                    coverage['unreadable'] += 1
                    continue
                total += len(raw)
                files.append({'path': relative, 'text': source, 'configKeys': keys})
                identities.append((relative, digest(raw)))
                coverage['scanned'] += 1
    helper=Path(__file__).with_name('migration_usage.cjs');helper_digest=digest(helper.read_bytes())
    node=shutil.which('node')
    if not node:return _app(reason='USAGE_NODE_UNAVAILABLE')
    try:
        node=Path(node).resolve(strict=True)
        if node.is_relative_to(project):
            return _app(reason='PROJECT_NODE_EXECUTABLE_REJECTED')
    except (OSError,RuntimeError):
        return _app(reason='USAGE_NODE_UNAVAILABLE')
    input_path=state/('migration-usage-input-'+uuid.uuid4().hex+'.json')
    created=False
    try:
        _plain_under(input_path,state)
        state.mkdir(parents=True,exist_ok=True)
        with input_path.open('xb') as stream:
            created=True
            stream.write(encoded({'packages':packages,'files':files}))
        remaining=deadline-time.monotonic()
        if remaining<=0:return _app(reason='USAGE_DEADLINE')
        observed=run_process([str(node),str(helper),str(parser),str(input_path)],cwd=str(helper.parent),env={k:v for k,v in os.environ.items() if k.upper() not in {'NODE_OPTIONS','NODE_PATH','NODE_V8_COVERAGE'}},timeout=min(10,remaining),max_output_bytes=65536)
        if observed.timed_out or observed.returncode:return _app(reason='USAGE_PARSER_INCOMPLETE')
        parsed=json.loads(observed.stdout)
        if (parsed.get('parserVersion')!=_PARSER_VERSION or parsed.get('sourceEvaluationPerformed') is not False
                or parsed.get('projectImportsResolved') is not False):return _app(reason='USAGE_PARSER_PROTOCOL_INVALID')
        if digest(parser.read_bytes())!=expected or digest(helper.read_bytes())!=helper_digest:return _app(status='STALE',reason='USAGE_IMPLEMENTATION_CHANGED')
        for parent, inputs in groupby(identities, key=lambda row: (project / row[0]).parent):
            with bound_directory(project, parent, root_handle) as binding:
                for relative, expected_sha in inputs:
                    if time.monotonic() >= deadline:
                        return _app(reason='USAGE_DEADLINE')
                    if digest(_read_bytes(project / relative, 131072, parent_binding=binding)) != expected_sha:
                        return _app(status='STALE', reason='USAGE_SOURCE_CHANGED')
        coverage.update(parseErrors=parsed['parseErrors'],dynamicLoads=parsed['dynamicLoads'],aliasLoads=parsed['aliasLoads'],dynamicConfigs=parsed['dynamicConfigs'],omittedObservations=parsed['omittedObservations'],omittedSymbols=parsed['omittedSymbols'])
        incomplete=any(coverage[x] for x in ['unreadable','unsupported','limitReached','parseErrors','dynamicLoads','aliasLoads','dynamicConfigs','omittedObservations','omittedSymbols'])
        result.update(status='UNKNOWN' if incomplete else ('OBSERVED_SYNTAX_ONLY' if parsed['observations'] else 'NOT_OBSERVED_SCOPED'),
            reason='PARTIAL_OR_DYNAMIC_SCOPE' if incomplete else 'DECLARED_JS_TS_SYNTAX_SCOPE_ONLY',sourceIdentity=source_identity,
            localCoverage=coverage,observations=parsed['observations'],fileManifestDigest=digest(encoded(identities)),
            parser={'name':'typescript','version':_PARSER_VERSION,'sha256':expected,'helperSha256':helper_digest},parserReportedCpuSeconds=parsed.get('parserReportedCpuSeconds'),
            absenceClaimScope='INSPECTED_JS_TS_FILES_ONLY_NOT_RUNTIME_OR_WHOLE_PROJECT')
        return result
    except (OSError,ValueError,KeyError,TypeError,GoalContractError):return _app(reason='USAGE_INPUT_OR_OUTPUT_UNAVAILABLE')
    finally:
        if created:
            input_path.unlink(missing_ok=True)
