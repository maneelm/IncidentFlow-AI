import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "container_verifier", Path(__file__).resolve().parents[3] / "scripts/verify_containers.py"
)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    monkeypatch.setattr(verifier, "PRIVATE_VALUES", set())
    return verifier.Verification()


def test_diagnostic_reports_exact_check_expected_and_observed():
    with pytest.raises(verifier.VerificationFailure) as failure:
        verifier.expect_equal("http.ready.status", 502, 200)
    assert json.loads(str(failure.value)) == {
        "check": "http.ready.status",
        "expected": 200,
        "observed": 502,
    }


def test_boolean_contract_does_not_accept_a_numeric_substitute():
    with pytest.raises(verifier.VerificationFailure):
        verifier.expect_equal("provider.fake.synthetic", 1, True)


def test_missing_security_header_reports_required_policy_and_absence():
    with pytest.raises(verifier.VerificationFailure) as failure:
        verifier.expect_contains("http.spa.frame_ancestors", None, "frame-ancestors 'none'")
    assert json.loads(str(failure.value))["observed"] is None
    assert json.loads(str(failure.value))["expected"] == {"contains": "frame-ancestors 'none'"}


def test_probe_cleanup_cannot_address_a_container_from_another_project(isolated, monkeypatch):
    def inspect(args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=json.dumps(
                [{"Config": {"Labels": {"com.docker.compose.project": "incidentflow"}}}]
            ),
        )

    monkeypatch.setattr(verifier, "run", inspect)
    with pytest.raises(verifier.VerificationFailure, match="probe.project_label"):
        isolated.verify_probe_owner(f"{isolated.project}-claim-probe")


def test_diagnostics_redact_registered_credentials_headers_and_sensitive_fields(monkeypatch):
    secret = "private-fixture-value"
    monkeypatch.setattr(verifier, "PRIVATE_VALUES", {secret})
    error = verifier.VerificationFailure(
        "synthetic.check",
        True,
        {
            "detail": f"prefix {secret}; Bearer opaque-value",
            "authorization": "other-private-value",
            "api_key": "gsk_privatevalue",
        },
    )
    output = str(error)
    for value in [secret, "opaque-value", "other-private-value", "gsk_privatevalue"]:
        assert value not in output
    assert "[redacted]" in output


def test_browser_failure_retains_assertion_location_and_primitive_values_only():
    report = {
        "suites": [
            {
                "specs": [
                    {
                        "file": "e2e/workflows.spec.ts",
                        "line": 313,
                        "tests": [
                            {
                                "results": [
                                    {
                                        "status": "failed",
                                        "errors": [
                                            {
                                                "location": {
                                                    "file": "/checkout/e2e/workflows.spec.ts",
                                                    "line": 359,
                                                },
                                                "message": (
                                                    "Error: assertion\n"
                                                    "Expected: \u001b[32mtrue\u001b[0m\n"
                                                    "Received: false\n"
                                                    "Bearer private-credential\nprivate prompt"
                                                ),
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        ]
    }
    output = verifier.browser_failures(report)
    assert output == [
        {"file": "workflows.spec.ts", "line": 359, "expected": True, "observed": False}
    ]
    assert "private" not in json.dumps(output)


def test_browser_timeout_omits_raw_error_payload():
    report = {
        "suites": [
            {
                "suites": [
                    {
                        "specs": [
                            {
                                "file": "test.spec.ts",
                                "line": 20,
                                "tests": [
                                    {
                                        "results": [
                                            {
                                                "status": "timedOut",
                                                "errors": [{"message": "token=private timeout"}],
                                            }
                                        ]
                                    }
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    assert verifier.browser_failures(report) == [
        {"file": "test.spec.ts", "line": 20, "expected": "passed", "observed": "timedOut"}
    ]


def test_external_failure_never_displays_stdout_stderr_or_command_secrets(monkeypatch):
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 7, stdout="secret-response", stderr="secret-driver-error"
        ),
    )
    with pytest.raises(verifier.VerificationFailure) as failure:
        verifier.run(["docker", "private-argument"], check="compose.exec")
    assert json.loads(str(failure.value)) == {"check": "compose.exec", "expected": 0, "observed": 7}


def test_command_timeout_is_named_without_exception_output(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("private-command", 1, output="secret-output")

    monkeypatch.setattr(verifier.subprocess, "run", timeout)
    with pytest.raises(verifier.VerificationFailure) as failure:
        verifier.run(["docker"], timeout=1, check="compose.up")
    assert json.loads(str(failure.value)) == {
        "check": "compose.up",
        "expected": "finish within 1s",
        "observed": "timed_out",
    }


@pytest.mark.parametrize("project", ["incidentflow", "", "incidentflow-verify-../incidentflow"])
def test_normal_or_invalid_projects_cannot_be_addressed(isolated, monkeypatch, project):
    calls = []
    monkeypatch.setattr(verifier, "run", lambda *args, **kwargs: calls.append(args))
    isolated.project = project
    with pytest.raises(verifier.VerificationFailure):
        isolated.compose("down")
    assert calls == []


def test_another_valid_verification_project_cannot_be_substituted(isolated):
    isolated.project = "incidentflow-verify-000000000000"
    with pytest.raises(verifier.VerificationFailure, match="scope.original_project"):
        isolated.cleanup()


def test_existing_scope_is_not_claimed_or_deleted(isolated, monkeypatch):
    calls = []

    def existing(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="existing-container\n")

    monkeypatch.setattr(verifier, "run", existing)
    with pytest.raises(verifier.VerificationFailure, match="scope.no_existing_container"):
        isolated.claim_project()
    isolated.cleanup()
    assert len(calls) == 1
    assert not isolated.envfile.exists()


@pytest.mark.parametrize("flag", ["-v", "--volumes"])
def test_unchecked_compose_volume_deletion_is_never_allowed(isolated, flag):
    with pytest.raises(verifier.VerificationFailure, match="scope.no_unchecked_volume_deletion"):
        isolated.compose("down", flag)


def cleanup_commands(isolated, monkeypatch, *, volume=None, project_label=None, down_code=0):
    calls = []
    volume = volume or f"{isolated.project}_postgres_data"
    label = project_label or isolated.project
    isolated._claimed = True

    def fake(args, **kwargs):
        calls.append(args)
        output = ""
        code = 0
        if args[:3] == ["docker", "volume", "ls"]:
            output = volume + "\n"
        elif args[:3] == ["docker", "volume", "inspect"]:
            output = json.dumps(
                [
                    {
                        "Labels": {
                            "com.docker.compose.project": label,
                            "com.docker.compose.volume": "postgres_data",
                        }
                    }
                ]
            )
        elif args[:2] == ["docker", "compose"]:
            code = down_code
        return subprocess.CompletedProcess(args, code, stdout=output)

    monkeypatch.setattr(verifier, "run", fake)
    return calls


def test_cleanup_removes_only_exact_label_verified_new_volume_and_private_fixture(
    isolated, monkeypatch
):
    normal_env = verifier.ROOT / ".env"
    normal_env.write_text("existing private environment", encoding="utf-8")
    calls = cleanup_commands(isolated, monkeypatch)
    isolated.cleanup()
    assert ["docker", "volume", "rm", f"{isolated.project}_postgres_data"] in calls
    assert all("-v" not in call and "--volumes" not in call for call in calls)
    assert not isolated.directory.exists()
    assert normal_env.read_text(encoding="utf-8") == "existing private environment"


@pytest.mark.parametrize(
    "volume,label",
    [
        ("incidentflow_postgres_data", None),
        (None, "incidentflow"),
    ],
)
def test_cleanup_refuses_development_volume_names_or_mismatched_labels(
    isolated, monkeypatch, volume, label
):
    calls = cleanup_commands(isolated, monkeypatch, volume=volume, project_label=label)
    with pytest.raises(verifier.VerificationFailure):
        isolated.cleanup()
    assert not any(call[:3] == ["docker", "volume", "rm"] for call in calls)


def test_failed_container_shutdown_never_deletes_the_volume(isolated, monkeypatch):
    calls = cleanup_commands(isolated, monkeypatch, down_code=1)
    with pytest.raises(verifier.VerificationFailure, match="cleanup.compose_down"):
        isolated.cleanup()
    assert not any(call[:2] == ["docker", "volume"] for call in calls)


def test_unexpected_error_reports_stage_location_and_class_without_private_exception(
    isolated, capsys
):
    isolated.stage = "seed"
    try:
        raise RuntimeError("private-driver-password")
    except RuntimeError as error:
        verifier.report_failure(isolated, error)
    output = capsys.readouterr().out
    assert "stage=seed" in output and "RuntimeError" in output
    assert "private-driver-password" not in output


def test_inherited_development_connection_and_project_settings_are_not_forwarded(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    for key in ["DATABASE_URL", "CONTAINER_DATABASE_URL", "COMPOSE_PROJECT_NAME", "GROQ_API_KEY"]:
        monkeypatch.setenv(key, "must-not-be-forwarded")
    instance = verifier.Verification()
    assert not any(
        key in instance.env
        for key in [
            "DATABASE_URL",
            "CONTAINER_DATABASE_URL",
            "COMPOSE_PROJECT_NAME",
            "GROQ_API_KEY",
        ]
    )
    assert instance.values["GROQ_API_KEY"] == ""


def test_browser_deadline_diagnostics_do_not_include_command_or_captured_credentials(
    isolated, capsys
):
    isolated.stage = "browser"
    error = subprocess.TimeoutExpired(["private-command"], 180, output="private-output")
    verifier.report_failure(isolated, error)
    output = capsys.readouterr().out
    assert "stage=browser" in output and "finish within 180s" in output
    assert "private" not in output


@pytest.mark.parametrize("phase", ["verification", "cleanup"])
def test_main_always_cleans_and_returns_failure_for_failed_checks(monkeypatch, capsys, phase):
    calls = []

    class FakeVerification:
        project = "incidentflow-verify-000000000000"
        stage = "verification"

        def verify(self):
            calls.append("verify")
            if phase == "verification":
                raise verifier.VerificationFailure("browser.assertions", [], [{"line": 359}])

        def cleanup(self):
            calls.append("cleanup")
            self.stage = "cleanup"
            if phase == "cleanup":
                raise verifier.VerificationFailure("cleanup.compose_down", 0, 1)

    monkeypatch.setattr(verifier, "Verification", FakeVerification)
    monkeypatch.setattr(verifier.sys, "argv", ["verify_containers.py"])
    with pytest.raises(SystemExit) as result:
        verifier.main()
    assert result.value.code == 1
    assert calls == ["verify", "cleanup"]
    assert "PASS container acceptance verification" not in capsys.readouterr().out
