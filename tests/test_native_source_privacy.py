"""Compile real C/C++ nested targets; inspect macros and debug build paths."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeSourcePrivacy(unittest.TestCase):
    def test_release_has_no_build_root(self):
        self.prove_build('Release')

    def test_debug_has_no_build_root(self):
        self.prove_build('RelWithDebInfo')

    def prove_build(self, config):
        with tempfile.TemporaryDirectory(prefix='native-privacy-') as temp:
            root = Path(temp)
            source, external, build = (root / n for n in ('source with spaces', 'dependency with spaces', 'build with spaces'))
            source.mkdir(); external.mkdir()
            (external / 'fixture.c').write_text('const char* dependency_file(void) { return __FILE__; }\n')
            (external / 'header.h').write_text('inline const char* header_file() { return __FILE__; }\n')
            (external / 'CMakeLists.txt').write_text('add_library(dependency STATIC fixture.c)\n')
            (source / 'main.cpp').write_text('#include <cstdio>\n#include <header.h>\nextern "C" const char* dependency_file(void);\nint main() { std::puts(__FILE__); std::puts(dependency_file()); std::puts(header_file()); }\n')
            helper = (ROOT / 'runtime/cmake/SourcePrivacy.cmake').as_posix()
            (source / 'CMakeLists.txt').write_text(
                'cmake_minimum_required(VERSION 3.22)\nproject(privacy_probe LANGUAGES C CXX)\n'
                'add_subdirectory("${UID_FRONTEND_SOURCE}" dependency)\n'
                'add_executable(probe main.cpp)\ntarget_link_libraries(probe PRIVATE dependency)\n'
                'target_include_directories(probe PRIVATE "${UID_FRONTEND_SOURCE}")\n')
            def run(*args):
                result = subprocess.run(list(map(str, args)), capture_output=True, timeout=180)
                self.assertEqual(result.returncode, 0, (result.stdout + result.stderr).decode(errors='replace'))
                return result
            run('cmake', '-S', source, '-B', build, '-DCMAKE_BUILD_TYPE='+config,
                '-DCMAKE_PROJECT_INCLUDE=' + helper, '-DUID_FRONTEND_SOURCE=' + str(external))
            compiled = run('cmake', '--build', build, '--config', config, '--parallel', '2')
            candidates = [build / 'probe', build / config / 'probe.exe', build / 'probe.exe']
            executable = next(p for p in candidates if p.is_file())
            lines = run(executable).stdout.decode().replace('\\', '/').splitlines()
            diagnostic = (compiled.stdout + compiled.stderr).decode(errors='replace')
            project = build / 'probe.vcxproj'
            if project.is_file():
                diagnostic += '\n' + '\n'.join(line for line in project.read_text().splitlines()
                    if '<AdditionalOptions>' in line)
            self.assertEqual(lines, ['/aii-source/entry/main.cpp', '/aii-deps/UID_FRONTEND_SOURCE/fixture.c',
                                     '/aii-deps/UID_FRONTEND_SOURCE/header.h'],
                             diagnostic)
            data = executable.read_bytes()
            for private in (str(source), str(external), str(build)):
                for spelling in (private, private.replace('\\', '/')):
                    self.assertFalse(spelling.encode() in data, 'private ASCII build path remains')
                    self.assertFalse(spelling.encode('utf-16-le') in data, 'private UTF-16 build path remains')


if __name__ == '__main__':
    unittest.main()
