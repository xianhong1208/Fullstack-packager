"""
Unit tests for guaranteed failure explanations.
Source: app/services/error_diagnosis.py

The measured problem this fixes: of 174 real failures, only 22 carried a
diagnosis. The caller persisted a result only when a rule matched, so 87% of
the time a user saw a red "失敗" and nothing else — which is how one project
accumulated 55 retries against 3 successes in five weeks. Nobody was ignoring
the guidance; there wasn't any.

So the property under test is not "does the matcher work" but "is a failure
ever left unexplained".
"""

import pytest

from app.services.error_diagnosis import (
    build_failure_report,
    diagnose_build_failure,
    extract_error_lines,
)


class TestNeverUnexplained:
    """The guarantee: a failed build always gets something actionable."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "log",
        [
            "some completely unrecognized output\nprocess exited",
            "",
            "\n\n\n",
            "gibberish ζ ∂ ∫ 中文亂碼",
        ],
    )
    def test_tc_fail_001_unmatched_logs_still_get_a_diagnosis(self, log):
        """TC-FAIL-001: build_failure_report — never returns an empty diagnosis."""
        report = build_failure_report(log)
        assert len(report["diagnosis"]) >= 1
        assert report["diagnosis"][0]["problem"]
        assert report["diagnosis"][0]["suggestion"]

    @pytest.mark.unit
    def test_tc_fail_002_names_the_failing_stage_when_nothing_matches(self):
        """TC-FAIL-002: build_failure_report — the stage becomes the explanation.

        "It failed during preflight" is a far better starting point than
        "it failed", and it is available even when the log is unreadable.
        """
        report = build_failure_report("unrecognized noise", stage="preflight")
        assert "預檢" in report["diagnosis"][0]["problem"]
        assert report["failed_stage"] == "preflight"

    @pytest.mark.unit
    def test_tc_fail_003_tells_a_non_expert_what_to_do_with_the_output(self):
        """TC-FAIL-003: build_failure_report — the fallback suggests forwarding the lines.

        The audience includes people who cannot read a stack trace; the advice
        has to work for them too.
        """
        report = build_failure_report("error: something obscure broke")
        assert "管理員" in report["diagnosis"][0]["suggestion"]

    @pytest.mark.unit
    def test_tc_fail_004_matched_patterns_take_precedence_over_the_fallback(self):
        """TC-FAIL-004: build_failure_report — a real match is not replaced by the generic text."""
        log = "ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'"
        report = build_failure_report(log, stage="verify")

        problems = [d["problem"] for d in report["diagnosis"]]
        assert any("ABI" in p for p in problems)
        assert not any("無法自動判斷" in p for p in problems)


class TestExtractErrorLines:
    """Tests for the log excerpt shown when nothing else is available."""

    @pytest.mark.unit
    def test_tc_fail_010_picks_error_bearing_lines(self):
        """TC-FAIL-010: extract_error_lines — ordinary progress output is skipped."""
        log = "\n".join(
            [
                "Compiling module a",
                "Compiling module b",
                "ERROR: linker step failed",
                "Compiling module c",
            ]
        )
        assert extract_error_lines(log) == ["ERROR: linker step failed"]

    @pytest.mark.unit
    def test_tc_fail_011_returns_lines_oldest_first(self):
        """TC-FAIL-011: extract_error_lines — reading order is preserved for display."""
        log = "error: first problem\nnoise\nfatal: second problem"
        assert extract_error_lines(log) == ["error: first problem", "fatal: second problem"]

    @pytest.mark.unit
    def test_tc_fail_012_scans_from_the_end(self):
        """TC-FAIL-012: extract_error_lines — the newest errors win when capped.

        An early error is often a symptom; the last thing printed before the
        process died is usually the cause.
        """
        log = "\n".join(f"error: problem {i}" for i in range(20))
        lines = extract_error_lines(log, limit=3)
        assert lines == ["error: problem 17", "error: problem 18", "error: problem 19"]

    @pytest.mark.unit
    def test_tc_fail_013_deduplicates_repeated_errors(self):
        """TC-FAIL-013: extract_error_lines — a message repeated 100× is shown once."""
        log = "\n".join(["error: same thing"] * 100)
        assert extract_error_lines(log) == ["error: same thing"]

    @pytest.mark.unit
    def test_tc_fail_014_skips_absurdly_long_lines(self):
        """TC-FAIL-014: extract_error_lines — a minified blob is not a useful excerpt."""
        log = "error: " + ("x" * 900) + "\nfatal: real problem"
        assert extract_error_lines(log) == ["fatal: real problem"]

    @pytest.mark.unit
    def test_tc_fail_015_empty_input(self):
        """TC-FAIL-015: extract_error_lines — empty log yields no lines, not an error."""
        assert extract_error_lines("") == []


class TestNewPatterns:
    """Failure modes added because they occur and previously said nothing."""

    @pytest.mark.unit
    def test_tc_fail_020_git_auth_failure(self):
        """TC-FAIL-020: diagnose — a rejected git credential is explained."""
        log = "fatal: Authentication failed for 'https://gitlab.example.com/g/p.git'"
        problems = [d["problem"] for d in diagnose_build_failure(log)]
        assert any("Git 認證失敗" in p for p in problems)

    @pytest.mark.unit
    def test_tc_fail_021_missing_branch(self):
        """TC-FAIL-021: diagnose — a deleted or renamed branch is explained."""
        log = "fatal: Remote branch feature/gone not found in upstream origin"
        problems = [d["problem"] for d in diagnose_build_failure(log)]
        assert any("分支或標籤" in p for p in problems)

    @pytest.mark.unit
    def test_tc_fail_022_dependency_resolution(self):
        """TC-FAIL-022: diagnose — a dependency conflict is named as the project's own."""
        log = "error: No solution found when resolving dependencies for: torch>=2.0"
        diags = diagnose_build_failure(log)
        assert any("依賴" in d["problem"] for d in diags)
        # The point of this rule is telling the user it is not a packaging
        # setting they got wrong.
        assert any("不是打包設定" in d["suggestion"] for d in diags)

    @pytest.mark.unit
    def test_tc_fail_023_docker_daemon_down(self):
        """TC-FAIL-023: diagnose — an unreachable docker daemon is flagged as server-side."""
        log = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"
        diags = diagnose_build_failure(log)
        assert any("Docker daemon" in d["problem"] for d in diags)
        assert any("管理員" in d["suggestion"] for d in diags)

    @pytest.mark.unit
    def test_tc_fail_024_npm_install_distinct_from_build(self):
        """TC-FAIL-024: diagnose — dependency install is separated from the build script.

        They need different advice: a lock-file mismatch is fixed in the repo,
        a build error is fixed in the code.
        """
        log = "npm ERR! code EUSAGE\nnpm ci can only install with an existing package-lock.json"
        problems = [d["problem"] for d in diagnose_build_failure(log)]
        assert any("前端依賴安裝失敗" in p for p in problems)

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "log",
        [
            "504 Gateway Time-out",
            "<html><head><title>504 Gateway Time-out</title></head>",
            "upstream timed out (110: Connection timed out) while reading response header",
        ],
    )
    def test_tc_fail_026_gateway_timeout_blames_the_proxy_not_the_backend(self, log):
        """TC-FAIL-026: diagnose — a 504 is explained as an idle-timeout, not a crash.

        The failure mode that makes this hard to chase: nginx gives up and
        returns 504 while the backend keeps running and completes, so the
        backend's own log says success. Without this the reader concludes the
        upstream service is flaky.
        """
        diags = diagnose_build_failure(log)
        assert any("504" in d["problem"] for d in diags)
        matched = next(d for d in diags if "504" in d["problem"])
        # Must name the real mechanism and the fix that survives extra proxy
        # layers, since the user often cannot change the outermost nginx.
        assert "沒送出" in matched["suggestion"] or "靜默" in matched["suggestion"]
        assert "keepalive" in matched["suggestion"]

    @pytest.mark.unit
    def test_tc_fail_027_gateway_timeout_beats_the_generic_timeout_rule(self):
        """TC-FAIL-027: diagnose — 504 does not get the generic "raise the build timeout" advice.

        Both rules match the word "timeout"; the generic one points at
        GIT_CLONE_TIMEOUT, which has nothing to do with a proxied request.
        """
        diags = diagnose_build_failure("504 Gateway Time-out while reading response")
        problems = [d["problem"] for d in diags]
        assert problems[0].startswith("被反向代理判定逾時")

    @pytest.mark.unit
    def test_tc_fail_025_healthy_log_yields_nothing(self):
        """TC-FAIL-025: diagnose — a clean build is not given a phantom problem.

        The new rules widen matching, so this guards the other direction:
        normal output must not start producing warnings.
        """
        log = "\n".join(
            [
                "Nuitka-Options: Used command line options:",
                "Nuitka: Completed C compilation successfully.",
                "Copying libs/ ... done",
                "Binary exited cleanly",
            ]
        )
        assert diagnose_build_failure(log) == []


class TestDiagnosisActions:
    """One-click fixes attached to rules the platform can actually resolve.

    Actions are config overrides rather than UI instructions, so a rule can
    offer a fix without any frontend change. The discipline that matters is
    restraint: an action must only appear when the platform really can fix it,
    or the button becomes a lie.
    """

    @pytest.mark.unit
    def test_tc_act_001_abi_mismatch_offers_auto_detect(self):
        """TC-ACT-001: diagnose — the commonest failure carries its one-field fix.

        The correction is a single dropdown value; making someone re-enter the
        whole form to change it is how the same mistake gets resubmitted dozens
        of times.
        """
        log = "ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'"
        diag = next(d for d in diagnose_build_failure(log) if "ABI" in d["problem"])

        assert diag["action"]["overrides"] == {"python_version": "auto"}
        assert diag["action"]["label"]

    @pytest.mark.unit
    def test_tc_act_002_out_of_memory_lowers_parallelism(self):
        """TC-ACT-002: diagnose — OOM offers a reduced job count, not a rebuild-as-is.

        2 rather than 1: it cuts peak memory sharply while keeping some
        parallelism, so an already-slow build does not become far slower for no
        extra chance of succeeding.
        """
        diag = next(
            d for d in diagnose_build_failure("MemoryError during compilation")
            if "記憶體" in d["problem"]
        )
        assert diag["action"]["overrides"] == {"nuitka_jobs": 2}

    @pytest.mark.unit
    def test_tc_act_003_user_code_failures_offer_no_button(self):
        """TC-ACT-003: diagnose — no action when the fix is in the user's own code.

        A button implies the platform can resolve it. Attaching one to a
        dependency conflict or a dead docker daemon would send the user in a
        loop rebuilding an unchanged failure.
        """
        for log in (
            "error: No solution found when resolving dependencies for: torch>=2.0",
            "Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
            "fatal: Authentication failed for 'https://gitlab.example.com/g/p.git'",
        ):
            for d in diagnose_build_failure(log):
                assert d.get("action") is None, f"unexpected action on: {d['problem']}"

    @pytest.mark.unit
    def test_tc_act_004_overrides_are_valid_build_config_fields(self):
        """TC-ACT-004: diagnose — every override names a real BuildConfig field.

        A typo here produces a button that silently changes nothing, which is
        worse than having no button at all.
        """
        from app.schemas.task import BuildConfig

        fields = set(BuildConfig.model_fields)
        logs = [
            "ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'",
            "MemoryError during compilation",
        ]
        seen = 0
        for log in logs:
            for d in diagnose_build_failure(log):
                action = d.get("action")
                if not action:
                    continue
                seen += 1
                for key in action["overrides"]:
                    assert key in fields, f"{key} is not a BuildConfig field"
        assert seen >= 2
