import { describe, it, expect } from 'vitest'
import { assembleTaskCreate } from './buildConfig'

/**
 * Form values → API payload.
 *
 * Every field here falls back to a default, so a mistake never raises: it
 * quietly substitutes a default and the build runs with settings the user
 * never chose. That makes this the one place in the form worth asserting
 * field by field.
 */
const base = {
  project_name: 'token-server',
  project_type: 'backend_only',
  source_type: 'local',
  project_path: '/srv/projects/demo',
  pack_mode: 'full',
}

const assemble = (values: Record<string, unknown> = {}, dirs: string[] = [], dataDirs: string[] = []) =>
  assembleTaskCreate({ values: { ...base, ...values }, selectedDirs: dirs, selectedDataDirs: dataDirs })

describe('assembleTaskCreate — source modes are mutually exclusive', () => {
  it('git mode blanks project_path', () => {
    // Leaving a stale local path next to a git URL asks the backend to guess
    // which source the user meant.
    const { config } = assemble({
      source_type: 'git',
      git_url: 'https://gitlab.example.com/g/p.git',
      git_ref: 'main',
      project_path: '/srv/leftover',
    })
    expect(config.project_path).toBe('')
    expect(config.git_url).toBe('https://gitlab.example.com/g/p.git')
  })

  it('local mode blanks the git fields', () => {
    const { config } = assemble({
      source_type: 'local',
      git_url: 'https://gitlab.example.com/leftover.git',
      git_ref: 'stale-branch',
    })
    expect(config.git_url).toBe('')
    expect(config.git_ref).toBe('')
    expect(config.project_path).toBe('/srv/projects/demo')
  })

  it('trims whitespace pasted around a git URL', () => {
    const { config } = assemble({
      source_type: 'git',
      git_url: '  https://gitlab.example.com/g/p.git  ',
      git_ref: '  main  ',
    })
    expect(config.git_url).toBe('https://gitlab.example.com/g/p.git')
    expect(config.git_ref).toBe('main')
  })

  it('defaults git_ref_type to branch', () => {
    const { config } = assemble({ source_type: 'git', git_url: 'https://x/y.git' })
    expect(config.git_ref_type).toBe('branch')
  })
})

describe('assembleTaskCreate — directory pickers', () => {
  it('takes directories from the pickers, not the form', () => {
    // These live in component state outside the antd form; reading them from
    // form values would silently produce empty strings and drop the user's
    // entire selection.
    const { config } = assemble({}, ['src', 'utils'], ['config', 'static'])
    expect(config.extra_dirs).toBe('src, utils')
    expect(config.data_dirs).toBe('config, static')
  })

  it('empty pickers produce empty strings, not "undefined"', () => {
    const { config } = assemble()
    expect(config.extra_dirs).toBe('')
    expect(config.data_dirs).toBe('')
  })
})

describe('assembleTaskCreate — dependency groups', () => {
  it('wraps the single UI choice into the list the backend expects', () => {
    const { config } = assemble({ dependency_group: 'rocm' })
    expect(config.dependency_groups).toEqual(['rocm'])
  })

  it('yields an empty list when cleared', () => {
    expect(assemble({ dependency_group: undefined }).config.dependency_groups).toEqual([])
    expect(assemble({ dependency_group: '' }).config.dependency_groups).toEqual([])
  })

  it('does not emit a blank entry for a whitespace-only value', () => {
    // `['']` would become `uv sync --group ''` and fail the build.
    expect(assemble({ dependency_group: '   ' }).config.dependency_groups).toEqual([])
  })
})

describe('assembleTaskCreate — defaults', () => {
  it('applies the documented defaults when fields are untouched', () => {
    const { config } = assemble()
    expect(config.output_dir).toBe('dist')
    expect(config.python_version).toBe('auto')
    expect(config.entry_point).toBe('main.py')
    expect(config.frontend_dir).toBe('frontend')
    expect(config.frontend_build_command).toBe('build')
    expect(config.docker_base_image).toBe('python:3.13-slim')
    expect(config.docker_expose_port).toBe(8000)
  })

  it('nginx defaults match the server side', () => {
    // 300s rather than nginx's 60s: an idle timeout that short kills
    // speech-to-text and any call that returns nothing until it finishes.
    const { config } = assemble()
    expect(config.nginx_proxy_read_timeout).toBe('300s')
    expect(config.nginx_streaming).toBe(true)
    expect(config.nginx_gzip).toBe(true)
  })

  it('preserves an explicit false rather than replacing it with the default', () => {
    // The classic `||` bug: a user turning something off gets it turned back
    // on. Every boolean here must use ?? so false survives.
    const { config } = assemble({
      onefile: false,
      nginx_gzip: false,
      nginx_streaming: false,
      enable_anti_bloat: false,
      verify_after_build: false,
      docker_install_node: false,
    })
    expect(config.onefile).toBe(false)
    expect(config.nginx_gzip).toBe(false)
    expect(config.nginx_streaming).toBe(false)
    expect(config.enable_anti_bloat).toBe(false)
    expect(config.verify_after_build).toBe(false)
    expect(config.docker_install_node).toBe(false)
  })

  it('preserves nuitka_jobs = 0, which means "auto"', () => {
    // 0 is a meaningful value here, and `|| 0` would coincidentally work while
    // `|| 4` would not — asserting it keeps the intent from being refactored away.
    expect(assemble({ nuitka_jobs: 0 }).config.nuitka_jobs).toBe(0)
    expect(assemble({ nuitka_jobs: 8 }).config.nuitka_jobs).toBe(8)
  })

  it('keeps user values over defaults', () => {
    const { config } = assemble({
      output_dir: 'build',
      python_version: '3.12',
      entry_point: 'src/app.py',
      output_name: 'myapp',
    })
    expect(config.output_dir).toBe('build')
    expect(config.python_version).toBe('3.12')
    expect(config.entry_point).toBe('src/app.py')
    expect(config.output_name).toBe('myapp')
  })
})

describe('assembleTaskCreate — shape', () => {
  it('carries the project name at the top level, not inside config', () => {
    const { project_name, config } = assemble({ project_name: 'my-service' })
    expect(project_name).toBe('my-service')
    expect(config).not.toHaveProperty('project_name')
  })

  it('never emits undefined for a string field', () => {
    // undefined serialises away entirely, so the backend would fall back to
    // its own default — a second, invisible source of truth for the value.
    const { config } = assemble()
    for (const [key, value] of Object.entries(config)) {
      expect(value, `${key} is undefined`).not.toBeUndefined()
    }
  })
})
