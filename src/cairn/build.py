"""Explicit native builds into fresh directories, never stale output reuse."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import time
from .cairnc import compile_source, Parser, RUNTIME
from .project import Project, ProjectError

FLAGS = ['-std=c++20', '-O3', '-ffp-contract=off', '-fno-fast-math',
         '-fno-exceptions', '-fno-rtti', '-Wall', '-Wextra', '-Werror',
         '-Wno-unused-parameter', '-Wno-unused-variable', '-Wno-unused-but-set-variable']


def build(project: Project, *, output: Path | None = None, cxx: str = 'clang++',
          arch: str | None = None, kind: str | None = None, timeout: int = 60) -> dict:
    if platform.system() != 'Linux' or platform.machine() not in {'x86_64', 'AMD64'}:
        raise ProjectError('Native builds currently support Linux x86-64 only.')
    arch, kind = arch or project.arch, kind or project.kind
    if arch not in {'x86-64', 'x86-64-v3'} or kind not in {'library', 'exe'}:
        raise ProjectError('Unsupported build kind or architecture.')
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise ProjectError('Build timeout must be 1..300 seconds.')
    compiler = shutil.which(cxx)
    if not compiler:
        raise ProjectError(f'Native compiler unavailable: {cxx}. Nothing was downloaded.')
    generated, receipt = compile_source(project.source)
    if kind == 'exe':
        functions = {f.name: f for f in Parser(project.source).parse().functions}
        main = functions.get('main')
        if main is None or main.static or main.params or main.ret.name != 'i32' or main.ret.mode != 'value':
            raise ProjectError('An executable needs fn main() -> i32 with no arguments.')
        generated += '\nint main() { return static_cast<int>(cf_main()); }\n'
    # No manifest can select a compiler executable, flags, build script, or output path.
    out = output or project.root / 'build'
    if out.is_symlink():
        raise ProjectError('Build output must not be a symbolic link.')
    out.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[^A-Za-z0-9_-]', '_', project.name)[:64] or 'program'
    directory = Path(tempfile.mkdtemp(prefix=name + '-', dir=out.resolve()))
    cpp = directory / 'program.cpp'
    cpp.write_text(generated, encoding='utf-8')
    (directory / 'cairn_runtime.hpp').write_text(RUNTIME, encoding='utf-8')
    artifact = directory / ('lib' + name + '.so' if kind == 'library' else name)
    flags = [*FLAGS, '-march=' + arch]
    if kind == 'library':
        flags += ['-shared', '-fPIC']
    command = [compiler, *flags, str(cpp), '-o', str(artifact)]
    started = time.monotonic()
    record = {'schema': 'cairn.build/1', 'status': 'unknown', 'formal_status': 'not-verified',
              'project': project.receipt(), 'frontend': receipt, 'kind': kind, 'command': command,
              'generated_sha256': hashlib.sha256(generated.encode()).hexdigest(),
              'artifact': str(artifact), 'directory': str(directory)}
    try:
        record['compiler_version'] = subprocess.run([compiler, '--version'], check=True,
                capture_output=True, text=True, timeout=5).stdout[:10000]
        cp = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        record.update(status='native-built' if cp.returncode == 0 else 'native-build-failed',
                      exit_code=cp.returncode, stdout=cp.stdout[:32000], stderr=cp.stderr[:32000])
        if cp.returncode == 0:
            record['artifact_sha256'] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as error:
        record.update(status='unknown', message=str(error))
    record['elapsed_seconds'] = time.monotonic() - started
    (directory / 'receipt.json').write_text(json.dumps(record, indent=2) + '\n')
    return record
