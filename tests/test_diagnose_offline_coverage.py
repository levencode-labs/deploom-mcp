import hashlib
import json
import sqlite3
from contextlib import closing
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from diagnose_offline_coverage import diagnose


class SavedRunDiagnosisTests(unittest.TestCase):
    def test_saved_run_keeps_unknown_and_intersects_distinct_exact_pairs(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            rows = []
            for name, version, tag, latest in [
                ("sample", "2.0.0", "osv-unavailable", "3.0.0"),
                ("private-example", "1.0.0", "", "internal/non-registry"),
                ("second", "1.0.0", "", "1.0.0"),
                ("unresolved", "1.0.0", "", "1.0.0"),
            ]:
                rows.append(dict(project="example", package=name, kind="dependencies", requestedSpec=version,
                                 current=version, scanState="done", draftUnknown=tag, latest=latest, status="no-target"))
            rows[-1]["requestedSpec"] = "^1.0.0"
            rows[-1]["currentSecurityEvidence"] = {"status": "UNKNOWN", "reason": "CURRENT_VERSION_UNRESOLVED"}
            unknowns = [dict(row, reason="OSV/security state unknown for this package in this Draft run")
                        for row in rows[:2]]
            plan = json.dumps({"unknowns": unknowns, "proposals": [{"rows": rows}]}).encode()
            (directory / "plan.json").write_bytes(plan)
            result = {"runId": "test", "hashes": {"plan": hashlib.sha256(plan).hexdigest()},
                      "metadata": {"securityTotal": 4, "securityKnown": 1, "processed": 4, "processedTotal": 4}}
            result_file = directory / "result.json"
            result_file.write_text(json.dumps(result))
            inventory = directory / "inventory.json"
            inventory.write_text(json.dumps({"complete": True, "pairs": [
                ["sample", "1.0.0"], ["second", "1.0.0"], ["second", "1.0.0"], ["private-example", "1.0.0"], ["unresolved", "1.0.0"]]}))
            report = diagnose(result_file, inventory_path=inventory, focus="private-example")
            self.assertTrue(report["accountsForReportedUnknowns"])
            self.assertEqual({"LEGACY_REASON_NOT_RECORDED": 1, "NON_REGISTRY_SPEC": 1, "CURRENT_VERSION_UNRESOLVED": 1}, report["recordedReasons"])
            self.assertEqual(1, report["pairIntersection"]["sharedPairs"])
            self.assertEqual(2, report["pairIntersection"]["distinctDirectPairs"])
            self.assertEqual("NON_REGISTRY_SPEC", report["focusUnknownRows"][0]["recordedReason"])
            self.assertNotIn("private-example", json.dumps(report))
            db_sha = "a" * 64
            database = directory / (db_sha + ".sqlite")
            with closing(sqlite3.connect(database)) as conn:
                conn.execute("CREATE TABLE advisory(name,id,severity,versions,ranges)")
            manifest = {"schema": 1, "ecosystem": "npm", "sha256": db_sha,
                        "databaseSha256": hashlib.sha256(database.read_bytes()).hexdigest()}
            (directory / "active.json").write_text(json.dumps(manifest))
            result["planningOptions"] = {"securitySnapshot": {"status": "ready", "sha256": db_sha}}
            result_file.write_text(json.dumps(result))
            with mock.patch.dict("os.environ", {"DEPLOOM_MCP_PUBLIC_NAMES": "", "DEPLOOM_MCP_PRIVATE_SCOPES": ""}):
                report = diagnose(result_file, snapshot_dir=directory)
            self.assertEqual({"PUBLIC_PACKAGE_UNCONFIRMED": 1}, report["replayOfLegacyUnknownsWithSuppliedPolicy"])
            result["planningOptions"]["securitySnapshot"]["sha256"] = "b" * 64
            result_file.write_text(json.dumps(result))
            with self.assertRaisesRegex(ValueError, "edition differs"):
                diagnose(result_file, snapshot_dir=directory)
            (directory / "plan.json").write_bytes(plan + b" ")
            with self.assertRaisesRegex(ValueError, "does not match"):
                diagnose(result_file)
