"""Docker build worker for packaging projects as Docker images."""

import asyncio
import os
import pty
import re
import shutil
import time
from pathlib import Path
from textwrap import dedent

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.task import BuildConfig, TaskStatus, ProjectType, PackMode
from app.services.task_manager import task_manager


async def run_docker_build(
    task_id: str,
    config: BuildConfig,
    db: AsyncSession,
    precompiled: bool = False,
) -> bool:
    """Build a Docker image for the project.

    Args:
        task_id: Task ID for logging
        config: Build configuration
        db: Database session

    Returns:
        True if build succeeded, False otherwise
    """
    project_path = Path(config.project_path)

    # Check if docker is available
    if not shutil.which("docker"):
        await task_manager.append_log(task_id, "Error: Docker not found in PATH")
        return False

    await task_manager.append_log(task_id, "Docker found, preparing build...")

    # Determine image name
    image_name = config.docker_image_name
    if not image_name:
        image_name = f"{project_path.name}:latest"

    await task_manager.append_log(task_id, f"Image name: {image_name}")

    # Generate .dockerignore
    dockerignore_path = project_path / ".dockerignore"
    dockerignore_backup = None
    generated_dockerignore = False

    if precompiled:
        # Precompiled mode: always use our .dockerignore to ensure
        # output directory is not excluded (backup existing if present)
        if dockerignore_path.exists():
            dockerignore_backup = dockerignore_path.with_suffix(".dockerignore.bak")
            shutil.copy2(dockerignore_path, dockerignore_backup)
        dockerignore_path.write_text(_generate_dockerignore(precompiled=True))
        generated_dockerignore = True
        await task_manager.append_log(task_id, "Generated .dockerignore (precompiled)")
    elif not dockerignore_path.exists():
        dockerignore_path.write_text(_generate_dockerignore())
        generated_dockerignore = True
        await task_manager.append_log(task_id, "Generated .dockerignore")

    # Write .env file for frontend if configured (backup original, restore after build)
    # Skip for precompiled mode — frontend was already built on host
    env_file_path = None
    env_backup_content = None  # None = file didn't exist, str = original content
    dockerignore_modified = False
    original_dockerignore_content = None
    if not precompiled and config.frontend_env_content.strip() and config.project_type in (
        ProjectType.FRONTEND_ONLY, ProjectType.FULLSTACK
    ):
        env_filename = config.frontend_env_filename or ".env"
        fe_dir = project_path / config.frontend_dir
        if fe_dir.exists():
            env_file_path = fe_dir / env_filename
            if env_file_path.exists():
                env_backup_content = env_file_path.read_text(encoding="utf-8")
                await task_manager.append_log(task_id, f"Backed up original {env_filename}")
            env_file_path.write_text(config.frontend_env_content, encoding="utf-8")
            await task_manager.append_log(task_id, f"Wrote {env_filename} in {fe_dir} for Docker build")

            # Add negation to .dockerignore so Docker COPY can pick it up
            if dockerignore_path.exists():
                relative_env = f"{config.frontend_dir}/{env_filename}"
                original_dockerignore_content = dockerignore_path.read_text()
                ignore_content = original_dockerignore_content + f"\n# Allow env file for frontend build\n!{relative_env}\n"
                dockerignore_path.write_text(ignore_content)
                dockerignore_modified = True

    # Generate Dockerfile based on project type
    dockerfile_path = project_path / "Dockerfile.generated"

    try:
        dockerfile_content = _generate_dockerfile(config, precompiled=precompiled)
        dockerfile_path.write_text(dockerfile_content)
        await task_manager.append_log(task_id, "Generated Dockerfile")
        await task_manager.append_log(task_id, "")
        await task_manager.append_log(task_id, "=== Dockerfile ===")
        for line in dockerfile_content.split("\n")[:20]:
            await task_manager.append_log(task_id, line)
        if dockerfile_content.count("\n") > 20:
            await task_manager.append_log(task_id, "... (truncated)")
        await task_manager.append_log(task_id, "==================")

    except Exception as e:
        await task_manager.append_log(task_id, f"Error generating Dockerfile: {e}")
        return False

    # Build Docker image
    await task_manager.append_log(task_id, "")
    await task_manager.append_log(task_id, "=== Building Docker image ===")
    await task_manager.update_task(task_id, "status_msg", "Building Docker image...")
    await task_manager.update_task(task_id, "progress", 20)

    build_cmd = [
        "docker", "build",
        "-t", image_name,
        "-f", str(dockerfile_path),
        str(project_path),
    ]

    await task_manager.append_log(task_id, f"Command: {' '.join(build_cmd)}")

    success = await _run_docker_command(task_id, build_cmd, project_path)

    # Cleanup generated files
    try:
        dockerfile_path.unlink()
    except Exception:
        pass
    if generated_dockerignore:
        try:
            dockerignore_path.unlink()
        except Exception:
            pass
    # Restore .dockerignore if we modified an existing one (non-generated)
    if dockerignore_modified and not generated_dockerignore and original_dockerignore_content is not None:
        try:
            dockerignore_path.write_text(original_dockerignore_content)
        except Exception:
            pass
    # Restore original env file after Docker build
    if env_file_path:
        try:
            if env_backup_content is not None:
                env_file_path.write_text(env_backup_content, encoding="utf-8")
                await task_manager.append_log(task_id, f"Restored original {env_file_path.name}")
            else:
                env_file_path.unlink(missing_ok=True)
                await task_manager.append_log(task_id, f"Cleaned up {env_file_path.name}")
        except Exception:
            pass
    # Restore original .dockerignore if we backed it up (precompiled mode)
    if dockerignore_backup and dockerignore_backup.exists():
        try:
            shutil.move(str(dockerignore_backup), str(dockerignore_path))
        except Exception:
            pass

    if success:
        await task_manager.append_log(task_id, "")
        await task_manager.append_log(task_id, "=" * 50)
        await task_manager.append_log(task_id, f"Docker image built successfully: {image_name}")
        await task_manager.append_log(task_id, "")
        await task_manager.append_log(task_id, "To run the container:")
        await task_manager.append_log(task_id, f"  docker run --rm --network host {image_name}")
        await task_manager.append_log(task_id, "=" * 50)

        # Export Docker image as .tar.gz for download
        await task_manager.append_log(task_id, "")
        await task_manager.append_log(task_id, "Exporting Docker image...")
        await task_manager.update_task(task_id, "status_msg", "Exporting Docker image...")

        from app.config import get_settings

        docker_export_dir = Path(get_settings().docker_images_dir)
        docker_export_dir.mkdir(parents=True, exist_ok=True)

        safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", image_name)
        tar_path = docker_export_dir / f"{safe_name}.tar.gz"

        compress_argv, compressor_name = _compressor_argv(
            get_settings().docker_export_compress_threads
        )
        await task_manager.append_log(task_id, f"Compressor: {compressor_name}")

        try:
            started = time.monotonic()
            # Use os.pipe() for real fd piping between docker save and the
            # compressor, so a multi-GB image never lands in this process.
            read_fd, write_fd = os.pipe()

            docker_proc = await asyncio.create_subprocess_exec(
                "docker", "save", image_name,
                stdout=write_fd,
                stderr=asyncio.subprocess.PIPE,
            )
            os.close(write_fd)  # Close write end in parent so the compressor sees EOF

            tar_file = open(tar_path, "wb")
            gzip_proc = await asyncio.create_subprocess_exec(
                *compress_argv,
                stdin=read_fd,
                stdout=tar_file.fileno(),
                stderr=asyncio.subprocess.PIPE,
            )
            os.close(read_fd)  # Close read end in parent
            tar_file.close()

            _, gzip_err = await gzip_proc.communicate()
            await docker_proc.wait()

            if docker_proc.returncode == 0 and gzip_proc.returncode == 0 and tar_path.exists():
                elapsed = time.monotonic() - started
                size_mb = tar_path.stat().st_size / (1024 * 1024)
                rate = size_mb / elapsed if elapsed > 0 else 0
                await task_manager.append_log(
                    task_id,
                    f"Exported: {tar_path.name} ({size_mb:.1f} MB in {elapsed:.1f}s, {rate:.1f} MB/s)",
                )
            else:
                err_msg = gzip_err.decode().strip() if gzip_err else "Unknown error"
                await task_manager.append_log(task_id, f"Warning: Failed to export Docker image: {err_msg}")
                tar_path.unlink(missing_ok=True)
        except Exception as e:
            await task_manager.append_log(task_id, f"Warning: Failed to export Docker image: {e}")
            tar_path.unlink(missing_ok=True)

    return success


def _compressor_argv(threads: int) -> tuple[list[str], str]:
    """Pick the gzip-compatible compressor to pipe `docker save` into.

    pigz produces a standard gzip stream from multiple threads, so the output
    stays a normal .tar.gz that `docker load` and `tar -xzf` read unchanged —
    only the time to produce it differs. A multi-GB image is minutes of a
    single core with plain gzip on a machine that has 128.

    Falls back to gzip when pigz is absent rather than failing the export: the
    image is already built by this point, and refusing to package it over a
    missing optional tool would throw the whole build away.
    """
    pigz = shutil.which("pigz")
    if pigz:
        # -p caps the thread count. Deliberately NOT "all cores": this host
        # runs up to MAX_CONCURRENT_BUILDS Nuitka compiles at once, and a
        # 128-thread compressor would starve them for the tail of a build that
        # is otherwise finished. Most of the speedup is in the first few
        # threads anyway — beyond that disk throughput becomes the limit.
        return ([pigz, "-p", str(max(1, threads))], f"pigz -p {threads}")
    return (["gzip"], "gzip (單執行緒 — 安裝 pigz 可大幅加速)")


def _generate_dockerignore(precompiled: bool = False) -> str:
    """Generate a .dockerignore to reduce build context size."""
    lines = [
        ".venv",
        "__pycache__",
        "*.pyc",
        ".git",
        ".gitignore",
        ".env",
        ".env.*",
        "node_modules",
        "*.egg-info",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "Dockerfile*",
        ".dockerignore",
    ]
    # Don't exclude dist/build when precompiled — output may be there
    if not precompiled:
        lines.extend(["dist", "build"])
    return "\n".join(lines) + "\n"


_ENV_VAR_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")

# Commands considered safe for Docker RUN instructions.
# Only well-known package managers, file operations, and system commands are allowed.
ALLOWED_COMMAND_PREFIXES = (
    "apt-get ", "apt ", "apk ", "yum ", "dnf ", "pip ", "pip3 ",
    "npm ", "yarn ", "pnpm ", "bun ",
    "mkdir ", "chmod ", "chown ", "cp ", "mv ", "ln ",
    "echo ", "cat ", "sed ", "touch ",
    "useradd ", "groupadd ", "adduser ", "addgroup ",
)


def _render_docker_env_vars(env_vars: str) -> str:
    """Render environment variables as Dockerfile ENV instructions.

    Each non-empty line (KEY=VALUE) becomes a separate ENV instruction.
    Lines starting with # are treated as comments and skipped.
    Lines that don't match KEY=VALUE format are rejected.
    """
    lines = [line.strip() for line in env_vars.splitlines()
             if line.strip() and not line.strip().startswith("#")]
    if not lines:
        return ""

    valid_lines = []
    for line in lines:
        if not _ENV_VAR_PATTERN.match(line):
            raise ValueError(f"Invalid env var format (must be KEY=VALUE): {line!r}")
        valid_lines.append(line)

    result = "\n# User environment variables\n"
    result += "\n".join(f"ENV {line}" for line in valid_lines)
    return result + "\n"


def _render_custom_commands(commands: str) -> str:
    """Render custom shell commands as Dockerfile RUN instructions.

    Each non-empty line becomes a separate RUN instruction.
    Only commands from the allowlist are permitted for safety.
    """
    lines = [line.strip() for line in commands.splitlines() if line.strip()]
    if not lines:
        return ""

    for line in lines:
        if not any(line.startswith(prefix) for prefix in ALLOWED_COMMAND_PREFIXES):
            raise ValueError(
                f"Command not in allowlist: {line!r}. "
                f"Allowed prefixes: {', '.join(p.strip() for p in ALLOWED_COMMAND_PREFIXES)}"
            )

    result = "\n# Custom commands\n"
    result += "\n".join(f"RUN {line}" for line in lines)
    return result + "\n"


# nginx directive values are pasted by the user and land inside a generated
# config file. A quote or newline turns one directive into several, and a
# `#` comments out the rest of the line — all of which surface as an
# incomprehensible nginx syntax error minutes into a Docker build rather than
# as a form validation message.
_NGINX_VALUE_FORBIDDEN = re.compile(r"""["'\n\r;{}#\\]""")


def _validate_nginx_value(value: str, field: str) -> str:
    """Reject characters that would break out of a single nginx directive.

    Raises ValueError with the offending field named, so the failure is
    reported before the build starts instead of as an nginx parse error.
    """
    value = (value or "").strip()
    if _NGINX_VALUE_FORBIDDEN.search(value):
        raise ValueError(
            f"{field} 含有不允許的字元(引號、分號、大括號、# 或換行):{value!r}。"
            "這個值會直接寫進 nginx 設定檔,請只填單純的值,例如 '120s' 或 "
            "'http://backend:8000'。"
        )
    return value


_DEFAULT_BASE_IMAGE = "python:3.13-slim"


def _sanitize_base_image(base_image: str | None) -> str:
    """Guard against a base image whose tag was built from the literal python
    version "auto" (the auto-detect sentinel). `python:auto-slim` is not a real
    Docker tag, so `docker build` would fail at pull time. A Nuitka standalone
    binary bundles its own Python runtime, so the base image only supplies system
    libs — falling back to a valid default tag is safe. Defends old tasks and
    direct API callers even though the frontend no longer emits this.
    """
    image = (base_image or "").strip()
    if not image or re.search(r"python:auto\b", image, re.IGNORECASE):
        return _DEFAULT_BASE_IMAGE
    return image


def _generate_dockerfile(config: BuildConfig, precompiled: bool = False) -> str:
    """Generate Dockerfile based on project type and configuration.

    Backend/fullstack projects always use precompiled Nuitka binaries
    (compiled on host before Docker build).
    """
    project_type = config.project_type
    base_image = _sanitize_base_image(config.docker_base_image)
    port = config.docker_expose_port

    if project_type == ProjectType.FRONTEND_ONLY and not precompiled:
        return _generate_frontend_dockerfile(config, port)

    # Backend and fullstack always use precompiled binary
    return _generate_precompiled_dockerfile(config, base_image, port)


def _generate_frontend_dockerfile(config: BuildConfig, port: int) -> str:
    """Generate Dockerfile for frontend-only projects.

    Picks a builder stage that actually HAS the requested package manager:
      - npm        → node:20-alpine (npm bundled)
      - yarn       → node:20-alpine (yarn classic bundled)
      - pnpm       → node:20-alpine + `corepack enable pnpm`
      - bun        → oven/bun:1-alpine (bun is not a node tool)
    Previously every tool was run on node:20-alpine, so pnpm/bun silently
    failed at build time with "not found".
    """
    frontend_dir = config.frontend_dir
    output_dir = config.frontend_output_dir
    tool = config.frontend_build_tool.value
    build_command = config.frontend_build_command

    # ── Builder stage selection per package manager ──
    if tool == "bun":
        builder_base = "oven/bun:1-alpine"
        enable_pm = ""
        lock_copies = (
            f"COPY {frontend_dir}/package*.json ./\n"
            f"        COPY {frontend_dir}/bun.lock* ./"
        )
    else:
        builder_base = "node:20-alpine"
        # corepack ships with node; enabling pnpm activates its shim. yarn
        # classic is already bundled, npm needs nothing — so only pnpm here.
        enable_pm = "RUN corepack enable pnpm" if tool == "pnpm" else ""
        lock_copies = (
            f"COPY {frontend_dir}/package*.json ./\n"
            f"        COPY {frontend_dir}/yarn.lock* ./\n"
            f"        COPY {frontend_dir}/pnpm-lock.yaml* ./"
        )

    # Reproducible install: use the lockfile (frozen) when one is present, fall
    # back to a plain install when the repo ships none. This matches the host
    # build path and keeps Docker images deterministic, without breaking
    # lock-less repos (a hard --frozen-lockfile would fail those).
    install_run = {
        "npm": "if [ -f package-lock.json ]; then npm ci --legacy-peer-deps; else npm install; fi",
        "yarn": "if [ -f yarn.lock ]; then yarn install --frozen-lockfile; else yarn install; fi",
        "pnpm": "if [ -f pnpm-lock.yaml ]; then pnpm install --frozen-lockfile; else pnpm install; fi",
        "bun": "if [ -f bun.lockb ] || [ -f bun.lock ]; then bun install --frozen-lockfile; else bun install; fi",
    }.get(tool, f"{tool} install")

    # ── nginx server config (user-configurable) ──
    body_size = _validate_nginx_value(
        config.nginx_client_max_body_size or "0", "nginx_client_max_body_size"
    )
    # 300s, not nginx's 60s. Measured against real usage: of 100 builds that
    # configured an API proxy, 96 ran at 60s and only 4 had raised it — those
    # four being the people who hit the wall and worked out why. A
    # speech-to-text or LLM call returns nothing at all until it finishes, so
    # the idle timer expires mid-request and the user gets a 504 that looks
    # like their backend crashed.
    read_timeout = _validate_nginx_value(
        config.nginx_proxy_read_timeout or "300s", "nginx_proxy_read_timeout"
    )

    gzip_block = ""
    if config.nginx_gzip:
        gzip_block = (
            "  gzip on;\n"
            "  gzip_comp_level 5;\n"
            "  gzip_min_length 1024;\n"
            "  gzip_proxied any;\n"
            "  gzip_vary on;\n"
            "  gzip_types text/plain text/css application/javascript\n"
            "             application/json image/svg+xml application/xml;\n"
        )

    # Buffering is nginx's default and is wrong for streaming APIs. With
    # proxy_buffering on, nginx collects the whole upstream response before
    # sending any of it, so an LLM streaming tokens over SSE arrives as one
    # block at the end — the feature silently stops working and looks like a
    # frontend bug. proxy_request_buffering on likewise makes nginx receive an
    # entire upload (a long audio file for STT) before the backend sees byte
    # one, adding the upload time to the user's wait.
    streaming_block = ""
    if getattr(config, "nginx_streaming", True):
        streaming_block = (
            "    proxy_buffering off;\n"
            "    proxy_request_buffering off;\n"
            "    proxy_cache off;\n"
            # Some upstreams emit this to ask a proxy not to buffer; honouring
            # it costs nothing and helps apps that already set it.
            "    proxy_set_header X-Accel-Buffering no;\n"
        )

    proxy_block = ""
    map_block = ""
    if config.docker_api_proxy:
        target = _validate_nginx_value(
            config.docker_api_proxy.rstrip("/"), "docker_api_proxy"
        )
        # Sending `Connection: upgrade` on every request — which is what a
        # hardcoded value does — breaks keepalive to the upstream and confuses
        # backends that check the header. The map makes it conditional: upgrade
        # only when the client actually asked for one, close otherwise.
        map_block = (
            "map $http_upgrade $connection_upgrade {\n"
            "  default upgrade;\n"
            # nginx accepts "" for the empty-string key. Written with DOUBLE
            # quotes on purpose: each config line is emitted as a
            # single-quoted shell argument, and a '' inside one is swallowed
            # by the shell's string concatenation, silently producing a map
            # entry with no key.
            '  ""      close;\n'
            "}\n\n"
        )
        proxy_block = (
            # `location /api` is a PREFIX match, so it also captures /api-docs,
            # /apiv2 and anything else merely starting with those characters.
            # `^~ /api/` matches the path segment and beats regex locations.
            f"  location ^~ /api/ {{\n"
            f"    proxy_pass {target};\n"
            f"    proxy_set_header Host $host;\n"
            f"    proxy_set_header X-Real-IP $remote_addr;\n"
            f"    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
            f"    proxy_set_header X-Forwarded-Proto $scheme;\n"
            f"    proxy_http_version 1.1;\n"
            f"    proxy_set_header Upgrade $http_upgrade;\n"
            f"    proxy_set_header Connection $connection_upgrade;\n"
            # Without this, a dead backend still takes nginx's default 75s to
            # give up and the page just appears frozen.
            f"    proxy_connect_timeout 10s;\n"
            # read_timeout is an IDLE timeout, not a total-duration cap: it
            # fires when the upstream has sent nothing for this long. That is
            # why it kills single-shot calls (STT, a non-streaming LLM reply)
            # while never cutting a slow response that keeps trickling. It is
            # also the WebSocket idle timeout, so an app without heartbeats
            # needs a value longer than its quiet periods.
            f"    proxy_read_timeout {read_timeout};\n"
            f"    proxy_send_timeout {read_timeout};\n"
            f"{streaming_block}"
            f"  }}\n"
        )

    nginx_conf = (
        f"{map_block}"
        f"server {{\n"
        f"  listen {port};\n"
        f"  client_max_body_size {body_size};\n"
        f"{gzip_block}"
        f"  root /usr/share/nginx/html;\n"
        f"  index index.html;\n"
        f"\n"
        f"{proxy_block}"
        # Bundlers emit content-hashed filenames (index-a1b2c3.js), so the file
        # at a given URL can never change — cache it hard. Without this every
        # visit re-validates every asset, which is most of the load time on a
        # site whose whole job is serving static files.
        f"  location /assets/ {{\n"
        f"    expires 1y;\n"
        f'    add_header Cache-Control "public, immutable";\n'
        f"    access_log off;\n"
        f"  }}\n"
        f"\n"
        # index.html must NOT be cached, or a deploy ships new hashed assets
        # that nobody's browser ever asks for.
        f"  location = /index.html {{\n"
        f'    add_header Cache-Control "no-cache";\n'
        f"  }}\n"
        f"\n"
        f"  location / {{\n"
        f"    try_files $uri $uri/ /index.html;\n"
        f"  }}\n"
        f"}}\n"
    )

    # One shell-quoted argument per config line. Continuations keep the
    # generated Dockerfile readable — the user is shown its first 20 lines in
    # the build log, and an unreadable wall of text there helps nobody.
    _conf_args = " \\\n            ".join(
        f"'{line}'" for line in nginx_conf.rstrip("\n").split("\n")
    )
    nginx_write_cmd = (
        f"printf '%s\\n' \\\n            {_conf_args} \\\n"
        f"            > /etc/nginx/conf.d/default.conf"
    )

    return dedent(f"""\
        # === Frontend Only Dockerfile ===

        # Build stage
        FROM {builder_base} AS builder

        WORKDIR /app
        {enable_pm}

        # Copy manifest + lockfiles (whichever exist)
        {lock_copies}

        # Install dependencies (frozen when a lockfile exists)
        RUN {install_run}

        # Copy source and build
        COPY {frontend_dir}/ ./
        RUN {tool} run {build_command}

        # Production stage - serve with nginx
        FROM nginx:alpine

        # Copy built files
        COPY --from=builder /app/{output_dir} /usr/share/nginx/html

        # Limit worker processes (default 'auto' spawns one per CPU core)
        RUN sed -i '/worker_processes/c\\worker_processes 2;' /etc/nginx/nginx.conf \\
            && rm -f /docker-entrypoint.d/30-tune-worker-processes.sh

        # Custom nginx config for SPA routing.
        # printf with one quoted argument per line, rather than a heredoc
        # (needs a BuildKit-era Dockerfile frontend) or a single `echo`
        # (cannot express multiple lines portably). Single quotes keep the
        # shell from expanding $uri / $host, which must reach nginx literally;
        # _validate_nginx_value() has already rejected any quote that could
        # close one of these strings early.
        RUN {nginx_write_cmd}

        # Validate at build time. Otherwise a bad directive ships an image that
        # crash-loops on startup, with the reason buried in container logs the
        # user may never think to look at.
        RUN nginx -t

        EXPOSE {port}

        CMD ["nginx", "-g", "daemon off;"]
    """)


def _generate_precompiled_dockerfile(config: BuildConfig, base_image: str, port: int) -> str:
    """Generate Dockerfile for precompiled Nuitka binary built on host.

    Only copies the compiled output into a slim runtime image.
    No compilation happens inside Docker.
    """
    entry_stem = Path(config.entry_point).stem
    binary_name = config.output_name if config.output_name else entry_stem
    output_dir = config.output_dir

    # Runtime extras
    extra_blocks = ""
    if config.docker_install_node:
        extra_blocks += (
            "\n# Install Node.js\n"
            "RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \\\n"
            "    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \\\n"
            "    && apt-get install -y --no-install-recommends nodejs \\\n"
            "    && apt-get purge -y curl \\\n"
            "    && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*\n"
        )
    extra_blocks += _render_docker_env_vars(config.docker_env_vars)
    extra_blocks += _render_custom_commands(config.docker_custom_commands)

    # Copy compiled binary
    if config.onefile:
        copy_binary = f"COPY {output_dir}/{binary_name} ./\nRUN chmod +x ./{binary_name}\n"
    else:
        copy_binary = f"COPY {output_dir}/{entry_stem}.dist/ ./\n"

    # Third-party packages (only in EXTERNAL mode — libs/ created by nuitka_worker)
    copy_libs = ""
    if config.pack_mode == PackMode.EXTERNAL:
        copy_libs = f"COPY {output_dir}/libs/ ./libs/\n"

    # Data directories (copied to output_dir by nuitka_worker).
    # Only COPY dirs that actually landed in dist/: nuitka_worker skips (with a
    # warning) any data_dir that doesn't exist in the project, so emitting a
    # blind `COPY dist/<dir>/` for a missing one makes `docker build` fail with
    # "COPY ... not found". Keep the two stages consistent — skip the same dirs.
    data_copy_lines = ""
    if config.data_dirs:
        dist_root = Path(config.project_path) / output_dir
        for data_dir in [d.strip() for d in config.data_dirs.split(",") if d.strip()]:
            if (dist_root / data_dir).is_dir():
                data_copy_lines += f"COPY {output_dir}/{data_dir}/ ./{data_dir}/\n"

    return dedent(f"""\
        # === Precompiled Backend Dockerfile ===
        FROM {base_image}
        WORKDIR /app
        RUN apt-get update && apt-get install -y --no-install-recommends \
            libpq5 \
            libxcb1 libglib2.0-0 libsm6 libxrender1 libxext6 libgl1 \
            zlib1g libxml2 libxslt1.1 \
            && apt-get clean && rm -rf /var/lib/apt/lists/*
    """) + extra_blocks + "\n# Copy precompiled Nuitka output\n" + copy_binary + copy_libs + data_copy_lines + dedent(f"""\
        ENV HOST=0.0.0.0
        ENV PORT={port}
        ENV LANG=C.UTF-8
        ENV LC_ALL=C.UTF-8
        ENV PYTHONIOENCODING=utf-8

        EXPOSE {port}

        CMD ["./{binary_name}"]
    """)


async def _run_docker_command(task_id: str, cmd: list[str], cwd: Path) -> bool:
    """Run a Docker command with output streaming.

    Note: Commands are constructed programmatically from validated config,
    not from raw user input.
    """
    master_fd, slave_fd = pty.openpty()

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=slave_fd,
            stderr=slave_fd,
            stdin=slave_fd,
            cwd=str(cwd),
            env={**os.environ, "TERM": "xterm-256color", "DOCKER_BUILDKIT": "1"},
        )
        os.close(slave_fd)

        await task_manager.set_process(task_id, process)
        # Non-blocking PTY reads on the event loop — no threads to leak. See the
        # note in nuitka_worker.run_nuitka_build for why the old
        # run_in_executor(os.read) pattern deadlocked under concurrency.
        os.set_blocking(master_fd, False)
        buffer = ""
        current_progress = 20

        def read_pty():
            try:
                return os.read(master_fd, 65536).decode("utf-8", errors="replace")
            except (BlockingIOError, InterruptedError):
                return ""
            except OSError:
                return ""

        while True:
            if process.returncode is not None:
                while True:
                    data = read_pty()
                    if not data:
                        break
                    buffer += data
                break

            data = read_pty()
            if data:
                buffer += data
            else:
                await asyncio.sleep(0.1)

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                clean_line = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", line).rstrip()
                if clean_line:
                    await task_manager.append_log(task_id, clean_line)

                    # Parse progress from Docker output
                    line_lower = clean_line.lower()
                    if "step" in line_lower:
                        match = re.search(r"step\s+(\d+)/(\d+)", line_lower)
                        if match:
                            step, total = int(match.group(1)), int(match.group(2))
                            current_progress = 20 + int((step / total) * 70)
                            await task_manager.update_task(task_id, "progress", current_progress)

        os.close(master_fd)
        await task_manager.set_process(task_id, None)
        ret_code = await process.wait()

        return ret_code == 0

    except Exception as e:
        await task_manager.append_log(task_id, f"Error running Docker command: {e}")
        await task_manager.set_process(task_id, None)
        try:
            os.close(master_fd)
        except Exception:
            pass
        return False
