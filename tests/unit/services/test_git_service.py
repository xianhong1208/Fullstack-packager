"""
Unit tests for the pure (non-subprocess) helpers of the Git source mode.
Source: app/services/git_service.py

These functions are the security boundary described in that module's
SECURITY INVARIANTS docstring: URL/ref validation gate every network call,
and token injection/masking decide whether a secret leaks into a log line.
"""

import pytest

from app.services.git_service import (
    GitUrlError,
    inject_token,
    mask_token_in_log,
    parse_dependency_groups,
    validate_git_ref,
    validate_git_url,
)

from .conftest import GITLAB_TOKEN, PRIVATE_IP_URL, PUBLIC_REPO_URL


class TestValidateGitUrl:
    """Tests for validate_git_url — the SSRF / scheme gate."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "url",
        [
            "https://gitlab.example.com/group/project.git",
            "http://gitlab.example.com/group/project.git",
            "https://gitlab.example.com:8443/group/project.git",
            "https://93.184.216.34/group/project.git",  # public IP literal
        ],
    )
    def test_tc_git_001_accepts_public_http_urls(self, url):
        """TC-GIT-001: validate_git_url — public http/https URLs accepted."""
        assert validate_git_url(url) is None

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ssh://git@gitlab.example.com/g/p.git",
            "git://gitlab.example.com/g/p.git",
            "ftp://gitlab.example.com/g/p.git",
        ],
    )
    def test_tc_git_002_rejects_non_http_schemes(self, url):
        """TC-GIT-002: validate_git_url — non-http(s) schemes rejected."""
        with pytest.raises(GitUrlError, match="Unsupported URL scheme"):
            validate_git_url(url)

    @pytest.mark.unit
    def test_tc_git_003_rejects_embedded_credentials(self):
        """TC-GIT-003: validate_git_url — userinfo in URL rejected."""
        with pytest.raises(GitUrlError, match="must not contain embedded credentials"):
            validate_git_url("https://user:secret@gitlab.example.com/g/p.git")

    @pytest.mark.unit
    def test_tc_git_004_rejects_missing_host(self):
        """TC-GIT-004: validate_git_url — URL without a host rejected."""
        with pytest.raises(GitUrlError, match="missing a host component"):
            validate_git_url("https:///group/project.git")

    @pytest.mark.unit
    def test_tc_git_005_rejects_dotdot_in_path(self):
        """TC-GIT-005: validate_git_url — '..' path traversal rejected."""
        with pytest.raises(GitUrlError, match="must not contain '\\.\\.'"):
            validate_git_url("https://gitlab.example.com/group/../../etc/p.git")

    @pytest.mark.unit
    @pytest.mark.parametrize("host", ["localhost", "ip6-localhost", "ip6-loopback"])
    def test_tc_git_006_rejects_blocked_hostnames(self, host):
        """TC-GIT-006: validate_git_url — localhost aliases always rejected."""
        with pytest.raises(GitUrlError, match="is not allowed"):
            validate_git_url(f"https://{host}/g/p.git")

    @pytest.mark.unit
    def test_tc_git_007_blocked_hostname_not_overridable_by_allowlist(self):
        """TC-GIT-007: validate_git_url — allowlist cannot re-enable localhost."""
        with pytest.raises(GitUrlError, match="is not allowed"):
            validate_git_url("https://localhost/g/p.git", allowed_hosts=["localhost"])

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "url",
        [
            "https://127.0.0.1/g/p.git",       # loopback
            "https://192.168.112.10/g/p.git",  # private
            "https://10.0.0.5/g/p.git",        # private
            "https://169.254.169.254/g/p.git", # link-local (cloud metadata)
            "https://0.0.0.0/g/p.git",         # unspecified
            "https://224.0.0.1/g/p.git",       # multicast
        ],
    )
    def test_tc_git_008_rejects_non_routable_ips_without_allowlist(self, url):
        """TC-GIT-008: validate_git_url — non-routable IP literals rejected when no allowlist."""
        with pytest.raises(GitUrlError, match="not a routable public address"):
            validate_git_url(url)

    @pytest.mark.unit
    def test_tc_git_009_allowlist_opts_in_private_ip(self):
        """TC-GIT-009: validate_git_url — allowlisted private IP is accepted."""
        assert validate_git_url(PRIVATE_IP_URL, allowed_hosts=["192.168.112.10"]) is None

    @pytest.mark.unit
    def test_tc_git_010_rejects_host_outside_allowlist(self):
        """TC-GIT-010: validate_git_url — host not in allowlist rejected."""
        with pytest.raises(GitUrlError, match="is not in the allowlist"):
            validate_git_url(PUBLIC_REPO_URL, allowed_hosts=["gitlab.internal.corp"])

    @pytest.mark.unit
    @pytest.mark.parametrize("url", ["", None])
    def test_tc_git_011_rejects_empty_url(self, url):
        """TC-GIT-011: validate_git_url — empty/None URL rejected."""
        with pytest.raises(GitUrlError, match="Git URL is required"):
            validate_git_url(url)

    @pytest.mark.unit
    def test_tc_git_012_rejects_overlong_url(self):
        """TC-GIT-012: validate_git_url — URL longer than 2048 chars rejected."""
        with pytest.raises(GitUrlError, match="too long"):
            validate_git_url("https://gitlab.example.com/" + "a" * 2100)

    @pytest.mark.unit
    def test_tc_git_013_host_match_is_case_insensitive(self):
        """TC-GIT-013: validate_git_url — uppercase host matches lowercase allowlist."""
        assert (
            validate_git_url(
                "https://GitLab.Example.COM/g/p.git",
                allowed_hosts=["gitlab.example.com"],
            )
            is None
        )


class TestValidateGitRef:
    """Tests for validate_git_ref — argv-injection defence for branch/tag names."""

    @pytest.mark.unit
    @pytest.mark.parametrize("ref", ["main", "release/1.2.0", "v1.0.0", "feature_x", "1.0"])
    def test_tc_git_020_accepts_normal_refs(self, ref):
        """TC-GIT-020: validate_git_ref — ordinary branch/tag names accepted."""
        assert validate_git_ref(ref) is None

    @pytest.mark.unit
    @pytest.mark.parametrize("ref", ["", None])
    def test_tc_git_021_rejects_empty_ref(self, ref):
        """TC-GIT-021: validate_git_ref — empty/None ref rejected."""
        with pytest.raises(GitUrlError, match="is required"):
            validate_git_ref(ref)

    @pytest.mark.unit
    def test_tc_git_022_rejects_overlong_ref(self):
        """TC-GIT-022: validate_git_ref — ref longer than 255 chars rejected."""
        with pytest.raises(GitUrlError, match="too long"):
            validate_git_ref("a" * 256)

    @pytest.mark.unit
    @pytest.mark.parametrize("ref", ["--upload-pack=touch /tmp/pwn", "-x"])
    def test_tc_git_023_rejects_option_lookalike_ref(self, ref):
        """TC-GIT-023: validate_git_ref — leading '-' rejected (would be a git option)."""
        with pytest.raises(GitUrlError, match="must not start with '-'"):
            validate_git_ref(ref)

    @pytest.mark.unit
    def test_tc_git_024_rejects_dotdot_ref(self):
        """TC-GIT-024: validate_git_ref — '..' rejected."""
        with pytest.raises(GitUrlError, match="must not contain '\\.\\.'"):
            validate_git_ref("main..evil")

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "ref",
        [
            "main branch",   # whitespace
            "main\tx",       # tab
            "main\nrm -rf",  # newline
            "main\x00",      # NUL
            "main~1",
            "main^2",
            "refs:main",
            "main?",
            "main*",
            "main[0]",
            "main\\x",
        ],
    )
    def test_tc_git_025_rejects_forbidden_characters(self, ref):
        """TC-GIT-025: validate_git_ref — whitespace/control/git-special chars rejected."""
        with pytest.raises(GitUrlError, match="forbidden characters"):
            validate_git_ref(ref)


class TestInjectToken:
    """Tests for inject_token — builds the credentialed URL handed to git."""

    @pytest.mark.unit
    def test_tc_git_030_injects_oauth2_userinfo(self):
        """TC-GIT-030: inject_token — token injected as oauth2 userinfo."""
        result = inject_token(PUBLIC_REPO_URL, GITLAB_TOKEN)
        assert result == f"https://oauth2:{GITLAB_TOKEN}@gitlab.example.com/group/project.git"

    @pytest.mark.unit
    def test_tc_git_031_preserves_port(self):
        """TC-GIT-031: inject_token — non-default port preserved."""
        result = inject_token("https://gitlab.example.com:8443/g/p.git", GITLAB_TOKEN)
        assert result == f"https://oauth2:{GITLAB_TOKEN}@gitlab.example.com:8443/g/p.git"

    @pytest.mark.unit
    @pytest.mark.parametrize("token", [None, ""])
    def test_tc_git_032_no_token_returns_url_unchanged(self, token):
        """TC-GIT-032: inject_token — falsy token returns the URL untouched."""
        assert inject_token(PUBLIC_REPO_URL, token) == PUBLIC_REPO_URL


class TestMaskTokenInLog:
    """Tests for mask_token_in_log — last line of defence before git output is logged."""

    @pytest.mark.unit
    def test_tc_git_040_masks_literal_token(self):
        """TC-GIT-040: mask_token_in_log — bare token occurrence replaced with ***."""
        result = mask_token_in_log(f"fatal: auth failed for {GITLAB_TOKEN}", GITLAB_TOKEN)
        assert GITLAB_TOKEN not in result
        assert result == "fatal: auth failed for ***"

    @pytest.mark.unit
    def test_tc_git_041_masks_oauth2_userinfo_without_token_arg(self):
        """TC-GIT-041: mask_token_in_log — oauth2 userinfo masked even when token is unknown."""
        text = "remote: https://oauth2:some-other-secret@gitlab.example.com/g/p.git not found"
        result = mask_token_in_log(text)
        assert "some-other-secret" not in result
        assert "oauth2:***@gitlab.example.com" in result

    @pytest.mark.unit
    def test_tc_git_042_masks_both_forms_in_one_pass(self):
        """TC-GIT-042: mask_token_in_log — literal token inside an oauth2 URL fully masked."""
        text = f"error cloning https://oauth2:{GITLAB_TOKEN}@gitlab.example.com/g/p.git"
        result = mask_token_in_log(text, GITLAB_TOKEN)
        assert GITLAB_TOKEN not in result
        assert result.endswith("https://oauth2:***@gitlab.example.com/g/p.git")

    @pytest.mark.unit
    def test_tc_git_043_leaves_clean_text_unchanged(self):
        """TC-GIT-043: mask_token_in_log — text with no secret is returned as-is."""
        text = "Cloning into 'project'... done."
        assert mask_token_in_log(text, GITLAB_TOKEN) == text

    @pytest.mark.unit
    @pytest.mark.parametrize("text", ["", None])
    def test_tc_git_044_empty_text_short_circuits(self, text):
        """TC-GIT-044: mask_token_in_log — empty/None text returned unchanged."""
        assert mask_token_in_log(text, GITLAB_TOKEN) == text


class TestParseDependencyGroups:
    """Tests for parse_dependency_groups — PEP 735 group picker for the UI."""

    @pytest.mark.unit
    def test_tc_git_050_returns_sorted_group_names(self):
        """TC-GIT-050: parse_dependency_groups — group names returned sorted."""
        toml_text = """
[project]
name = "x"

[dependency-groups]
test = ["pytest"]
dev = ["ruff"]
docs = ["mkdocs"]
"""
        assert parse_dependency_groups(toml_text) == ["dev", "docs", "test"]

    @pytest.mark.unit
    def test_tc_git_051_ignores_optional_dependencies(self):
        """TC-GIT-051: parse_dependency_groups — [project.optional-dependencies] is NOT a group."""
        toml_text = """
[project.optional-dependencies]
dev = ["ruff"]
"""
        assert parse_dependency_groups(toml_text) == []

    @pytest.mark.unit
    def test_tc_git_052_missing_table_returns_empty(self):
        """TC-GIT-052: parse_dependency_groups — absent table yields []."""
        assert parse_dependency_groups('[project]\nname = "x"\n') == []

    @pytest.mark.unit
    def test_tc_git_053_malformed_toml_returns_empty(self):
        """TC-GIT-053: parse_dependency_groups — unparseable TOML yields [] (not an exception)."""
        assert parse_dependency_groups("[dependency-groups\ndev = [") == []

    @pytest.mark.unit
    def test_tc_git_054_non_table_value_returns_empty(self):
        """TC-GIT-054: parse_dependency_groups — non-table dependency-groups yields []."""
        assert parse_dependency_groups('dependency-groups = "dev"\n') == []

    @pytest.mark.unit
    def test_tc_git_055_empty_text_returns_empty(self):
        """TC-GIT-055: parse_dependency_groups — empty input yields []."""
        assert parse_dependency_groups("") == []
