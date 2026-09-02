"""
Unit tests for choosing the image-export compressor.
Source: app/services/docker_worker._compressor_argv

`docker save | gzip` is single-threaded, so a multi-GB image spends minutes on
one core of a 128-core host. pigz emits a standard gzip stream from many
threads, so the output stays a plain .tar.gz that `docker load` reads unchanged.

The property that matters most here is the fallback: the image is already built
by the time this runs, and refusing to package it over a missing optional tool
would throw away an entire build.
"""

import pytest

from app.services.docker_worker import _compressor_argv

PATCH_WHICH = "app.services.docker_worker.shutil.which"


class TestCompressorSelection:
    @pytest.mark.unit
    def test_tc_cmp_001_uses_pigz_when_available(self, mocker):
        """TC-CMP-001: _compressor_argv — pigz is preferred and thread-capped."""
        mocker.patch(PATCH_WHICH, return_value="/usr/bin/pigz")

        argv, name = _compressor_argv(8)

        assert argv == ["/usr/bin/pigz", "-p", "8"]
        assert "pigz" in name

    @pytest.mark.unit
    def test_tc_cmp_002_falls_back_to_gzip(self, mocker):
        """TC-CMP-002: _compressor_argv — a missing pigz must not fail the export.

        The image exists at this point; losing it because an optional
        accelerator is absent would discard the whole build.
        """
        mocker.patch(PATCH_WHICH, return_value=None)

        argv, name = _compressor_argv(8)

        assert argv == ["gzip"]
        assert "gzip" in name

    @pytest.mark.unit
    def test_tc_cmp_003_fallback_name_says_how_to_speed_it_up(self, mocker):
        """TC-CMP-003: _compressor_argv — the log line names the missing tool.

        This string is written into the build log, which is the only place an
        operator would ever notice the export is running single-threaded.
        """
        mocker.patch(PATCH_WHICH, return_value=None)

        _, name = _compressor_argv(8)

        assert "pigz" in name

    @pytest.mark.unit
    @pytest.mark.parametrize("threads", [0, -1])
    def test_tc_cmp_004_thread_count_is_never_below_one(self, mocker, threads):
        """TC-CMP-004: _compressor_argv — a nonsense thread count cannot break the argv.

        `pigz -p 0` is an error, and a misconfigured value should degrade to
        working-but-slow rather than failing the export.
        """
        mocker.patch(PATCH_WHICH, return_value="/usr/bin/pigz")

        argv, _ = _compressor_argv(threads)

        assert argv[-1] == "1"

    @pytest.mark.unit
    def test_tc_cmp_005_thread_count_is_tuned_but_still_capped(self):
        """TC-CMP-005: settings — parallel enough for real images, short of the whole host.

        Sized from this deployment's actual artifacts: 162 recorded builds
        averaging 2 GB with a 16 GB maximum. At measured throughput a 16 GB
        export takes 8.4 min with gzip, 1.2 min at 8 threads and 28 s at 32 —
        so the jump from 8 to 32 is worth 26-41 s on the large builds, not the
        ~1 s a small test archive implies.

        Still capped rather than every core: the host runs up to
        MAX_CONCURRENT_BUILDS Nuitka compiles, and disk write throughput
        becomes the limit long before 128 threads.
        """
        from app.config import Settings

        default = Settings.model_fields["docker_export_compress_threads"].default
        assert default >= 16, "too low to matter on multi-GB images"
        assert default <= 64, "must not try to take the whole host"

    @pytest.mark.unit
    def test_tc_cmp_006_output_stays_a_plain_gzip_stream(self, mocker):
        """TC-CMP-006: _compressor_argv — no flags that change the output format.

        The artifact must remain loadable with `docker load` and `tar -xzf`;
        switching to a different container format would break every consumer.
        """
        mocker.patch(PATCH_WHICH, return_value="/usr/bin/pigz")

        argv, _ = _compressor_argv(4)

        # -p is thread count; anything else risks a non-gzip container.
        assert set(a for a in argv[1:] if a.startswith("-")) == {"-p"}
