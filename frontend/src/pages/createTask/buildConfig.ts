import type { BuildConfig, SourceType, TaskCreate } from '../../api/types'

/** Raw antd form values — deliberately loose, since the form is untyped. */
export type FormValues = Record<string, unknown>

export interface AssembleInput {
  values: FormValues
  /** Directory names ticked in the extra-source picker. */
  selectedDirs: string[]
  /** Directory names ticked in the data picker. */
  selectedDataDirs: string[]
}

/**
 * Turn form values into the payload the API is called with.
 *
 * Worth isolating because every field here has a `?? default` fallback, which
 * means a mistake in this mapping does not raise — it silently substitutes a
 * default and the build runs with settings the user never chose. A field name
 * typo, or forgetting to blank project_path in git mode, produces a working
 * request that builds the wrong thing.
 */
export function assembleTaskCreate({
  values,
  selectedDirs,
  selectedDataDirs,
}: AssembleInput): TaskCreate {
  const isGit = values.source_type === 'git'
  const str = (v: unknown, fallback = ''): string => {
    const s = typeof v === 'string' ? v : v == null ? '' : String(v)
    return s || fallback
  }

  const config: BuildConfig = {
    project_type: values.project_type as BuildConfig['project_type'],
    source_type: (values.source_type || 'local') as SourceType,

    // The two source modes are mutually exclusive on the wire. Sending a
    // stale project_path alongside a git URL — or vice versa — leaves the
    // backend to guess which one the user meant.
    git_url: isGit ? str(values.git_url).trim() : '',
    git_ref: isGit ? str(values.git_ref).trim() : '',
    git_ref_type: isGit
      ? ((values.git_ref_type || 'branch') as BuildConfig['git_ref_type'])
      : 'branch',
    project_path: isGit ? '' : str(values.project_path),

    output_dir: str(values.output_dir, 'dist'),
    python_version: str(values.python_version, 'auto'),
    entry_point: str(values.entry_point, 'main.py'),
    output_name: str(values.output_name),
    onefile: (values.onefile as boolean) ?? true,

    // Pickers live outside the form, so they are passed in rather than read
    // from values — reading them from the form would silently yield ''.
    extra_dirs: selectedDirs.join(', '),
    data_dirs: selectedDataDirs.join(', '),

    pack_mode: values.pack_mode as BuildConfig['pack_mode'],
    include_packages: str(values.include_packages),

    // Single-select in the UI, but the backend contract stays a list
    // (`uv sync --group <name>`). Wrap the one choice, or [] when cleared.
    dependency_groups: values.dependency_group
      ? [String(values.dependency_group).trim()].filter(Boolean)
      : [],

    frontend_dir: str(values.frontend_dir, 'frontend'),
    frontend_build_tool: (values.frontend_build_tool ||
      'npm') as BuildConfig['frontend_build_tool'],
    frontend_build_command: str(values.frontend_build_command, 'build'),
    frontend_output_dir: str(values.frontend_output_dir, 'dist'),
    frontend_env_filename: str(values.frontend_env_filename, '.env'),
    frontend_env_content: str(values.frontend_env_content),

    docker_enabled: (values.docker_enabled as boolean) ?? false,
    docker_image_name: str(values.docker_image_name),
    docker_base_image: str(values.docker_base_image, 'python:3.13-slim'),
    docker_expose_port: (values.docker_expose_port as number) || 8000,
    docker_env_vars: str(values.docker_env_vars),
    docker_custom_commands: str(values.docker_custom_commands),
    docker_install_node: (values.docker_install_node as boolean) ?? false,
    docker_api_proxy: str(values.docker_api_proxy),

    nginx_client_max_body_size: str(values.nginx_client_max_body_size, '0'),
    // 300s, not nginx's 60s: an idle timeout that short kills speech-to-text
    // and other calls that return nothing until they finish.
    nginx_proxy_read_timeout: str(values.nginx_proxy_read_timeout, '300s'),
    nginx_streaming: (values.nginx_streaming as boolean) ?? true,
    nginx_gzip: (values.nginx_gzip as boolean) ?? true,

    nuitka_jobs: (values.nuitka_jobs as number) ?? 0,
    enable_anti_bloat: (values.enable_anti_bloat as boolean) ?? true,
    include_package_data: str(values.include_package_data),
    generate_report: (values.generate_report as boolean) ?? false,
    verify_after_build: (values.verify_after_build as boolean) ?? true,
  }

  return { project_name: str(values.project_name), config }
}
