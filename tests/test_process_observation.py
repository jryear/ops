"""Unavailable observation must remain visible and can never authorize signals."""
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from opslib import ownership as ow, triage, machine, reap
from opslib.model import Report, emit

VALID = "501 4321 42 07:35 python worker.py\n-2 4322 1 04-20:13:09 /usr/libexec/daemon\n"


def result(stdout=VALID, code=0):
    return subprocess.CompletedProcess([], code, stdout, "private diagnostic must not leak")


class ProcessObservationTests(unittest.TestCase):
    def setUp(self):
        ow.reset_cache()

    def tearDown(self):
        ow.reset_cache()

    def test_failed_and_unusable_observation_never_looks_empty(self):
        cases = [PermissionError(), FileNotFoundError(), subprocess.TimeoutExpired("ps", 20),
                 result(code=1), result(""), result("garbage"), result(VALID + "bad row\n"),
                 result("501 10 x 07:35 cmd"), result("501 10 1 unknown cmd")]
        for case in cases:
            with self.subTest(case=type(case).__name__), patch.object(ow.subprocess, "run") as run:
                if isinstance(case, Exception): run.side_effect = case
                else: run.return_value = case
                with self.assertRaises(ow.ObservationUnavailable) as raised:
                    ow.list_processes()
                self.assertNotIn("private diagnostic", str(raised.exception))

    def test_live_format_signed_uid_and_elapsed(self):
        with patch.object(ow.subprocess, "run", return_value=result()):
            procs = ow.list_processes()
            self.assertEqual([p.uid for p in procs], [501, -2])
            self.assertEqual(procs[0].etime_s, 455)
            self.assertEqual(procs[1].etime_s, 418389)
            self.assertEqual(ow.list_processes(("no-match",)), [])

    def test_all_process_consumers_report_unavailable(self):
        for name, run in [("triage", lambda: triage.run("process")), ("reap", reap.run)]:
            with self.subTest(name=name), patch.object(ow, "list_processes", side_effect=ow.ObservationUnavailable("ps denied")):
                rep = run()
                self.assertEqual(rep.exit_code, 1)
                self.assertEqual(rep.checked["process_observation"], "unavailable")
                self.assertNotIn("candidates", rep.checked)
                self.assertNotIn("processes", rep.checked)
                output = io.StringIO()
                with redirect_stdout(output): self.assertEqual(emit(rep, True), 1)
                body = json.loads(output.getvalue())
                self.assertEqual(set(body), {"verb", "exit_code", "summary", "checked", "notes", "findings"})
                with redirect_stdout(io.StringIO()) as text_output: emit(rep, False)
                self.assertIn("unavailable", text_output.getvalue())
                self.assertNotIn("nothing needs attention", text_output.getvalue())

    def test_machine_keeps_independent_metrics_when_process_access_denied(self):
        with patch.object(machine, "_disk", side_effect=lambda rep: rep.checked.update(disk_free_gb=100)), patch.object(machine, "_docker"), patch.object(ow, "observe_command", side_effect=ow.ObservationUnavailable("ps denied")):
            rep = machine.run()
        self.assertEqual(rep.checked["disk_free_gb"], 100)
        self.assertEqual(rep.checked["cpu_observation"], "unavailable")
        self.assertEqual(rep.checked["process_observation"], "unavailable")
        self.assertNotIn("leaked_processes", rep.checked)
        self.assertGreaterEqual(rep.exit_code, 1)

    def test_cpu_bad_rows_nan_and_empty_are_not_healthy(self):
        for row in ["garbage", "nan 42 app", "inf 42 app", "10 nope app", "-1 42 app"]:
            with self.subTest(row=row), patch.object(ow, "observe_command", return_value=row):
                with self.assertRaises(ow.ObservationUnavailable): machine._cpu(Report("machine"))

    def test_cpu_success_and_housekeeping(self):
        rep = Report("machine")
        with patch.object(ow, "observe_command", return_value="90.0 42 /usr/libexec/fileproviderd\n0.0 43 /bin/sleep"):
            house, hogs = machine._cpu(rep)
        self.assertEqual(rep.checked["cpu_observation"], "available")
        self.assertTrue(house)
        self.assertFalse(hogs)
        self.assertEqual(rep.findings[0].kind, "os.housekeeping")

    def test_load_does_not_invent_a_cause_without_cpu_observation(self):
        rep = Report("machine"); rep.checked["cpu_observation"] = "unavailable"
        with patch.object(machine.os, "getloadavg", return_value=(99, 99, 99)):
            machine._load(rep, [], [])
        self.assertIn("cannot be determined", rep.findings[0].evidence)

    def test_ownership_failure_propagates(self):
        with patch.object(ow, "list_processes", return_value=[ow.Process(42, 1, 99999, "x", uid=501)]), patch.object(ow, "launchd_pids", side_effect=ow.ObservationUnavailable("launchctl denied")):
            rep = reap.run()
        self.assertEqual(rep.checked["process_observation"], "unavailable")
        self.assertNotIn("definitely_orphaned", rep.checked)

    def test_launchctl_malformed_is_unavailable(self):
        with patch.object(ow, "observe_command", return_value="not launchctl output"):
            with self.assertRaises(ow.ObservationUnavailable): ow.launchd_pids()

    def test_cwd_without_usable_path_is_unavailable(self):
        with patch.object(ow, "observe_command", return_value="p42\n"):
            with self.assertRaises(ow.ObservationUnavailable): ow._cwd_of(42)

    def test_orca_malformed_and_failed_envelope_unavailable(self):
        for value in ["garbage", "{}", '{"ok":false}', '{"ok":true,"result":{"repos":[null]}}']:
            ow.reset_cache()
            with self.subTest(value=value), patch.object(ow, "observe_command", return_value=value):
                with self.assertRaises(ow.ObservationUnavailable):
                    ow.observed_orca_rows(("repo", "list"), "repos")

    def test_app_bundle_discovery_rejects_bad_process_rows(self):
        with patch.object(ow.subprocess, "run", return_value=result("garbage")):
            with self.assertRaises(ow.ObservationUnavailable): ow.running_app_bundles()

    def test_destructive_path_never_signals_without_observation(self):
        for source in ["process", "ownership"]:
            with self.subTest(source=source), patch.object(reap.os, "kill") as kill, patch.object(ow, "require_ownership_observation") as ownership, patch.object(reap, "assess") as assess:
                (ownership if source == "ownership" else assess).side_effect = ow.ObservationUnavailable("denied")
                rep = reap.run(kill=True, pids=[42])
                self.assertEqual(rep.exit_code, 2)
                kill.assert_not_called()

    def test_successful_discovery_and_owned_process_are_preserved(self):
        process = ow.Process(42, 1, 99999, "x", uid=501, classification=ow.OWNED)
        with patch.object(reap, "assess", return_value=[process]), patch.object(ow, "require_ownership_observation"), patch.object(reap.os, "kill") as kill:
            rep = reap.run()
            self.assertEqual(rep.checked["active_owned"], 1)
            self.assertEqual(rep.checked["process_observation"], "available")
            self.assertEqual(rep.exit_code, 0)
            rep = reap.run(kill=True, pids=[42])
            self.assertEqual(rep.exit_code, 2)
            kill.assert_not_called()

    def test_new_inspection_failure_before_signal_refuses(self):
        process = ow.Process(42, 1, 99999, "x", uid=501, classification=ow.ORPHAN)
        with patch.object(reap, "assess", return_value=[process]), patch.object(ow, "require_ownership_observation"), patch.object(ow, "list_processes", side_effect=ow.ObservationUnavailable("new denial")), patch.object(reap.os, "kill") as kill:
            rep = reap.run(kill=True, pids=[42])
        kill.assert_not_called()
        self.assertEqual(rep.exit_code, 2)

    def test_no_sigkill_after_observation_disappears(self):
        process = ow.Process(42, 1, 99999, "x", uid=501, classification=ow.ORPHAN)
        with patch.object(reap, "assess", return_value=[process]), patch.object(ow, "require_ownership_observation"), patch.object(ow, "list_processes", side_effect=[[process], ow.ObservationUnavailable("new denial")]), patch.object(ow, "classify", return_value=process), patch.object(reap.os, "kill") as kill, patch.object(reap.time, "sleep"):
            rep = reap.run(kill=True, pids=[42])
        self.assertEqual(kill.call_count, 1)
        self.assertEqual(kill.call_args.args[1], reap.signal.SIGTERM)
        self.assertEqual(rep.exit_code, 2)


if __name__ == "__main__": unittest.main()
