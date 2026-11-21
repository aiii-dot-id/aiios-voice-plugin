import functools
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@functools.cache
def compiler_env():
    if os.name != "nt" or shutil.which("cl"):
        return dict(os.environ)
    root = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    vswhere = root / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        raise RuntimeError(f"no C++ compiler: cl is not on PATH and {vswhere} does not exist")
    install = subprocess.run(
        [str(vswhere), "-latest", "-products", "*", "-requires",
         "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
        check=True, capture_output=True, text=True, timeout=60).stdout.strip()
    vcvars = Path(install) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    if not install or not vcvars.is_file():
        raise RuntimeError("no C++ compiler: Visual Studio's C++ tools were not found by vswhere")
    with tempfile.TemporaryDirectory() as work:
        script = Path(work) / "env.bat"
        script.write_text(f'@call "{vcvars}" >nul\r\n@if errorlevel 1 exit /b 1\r\n@set\r\n',
                          encoding="utf-8", newline="")
        listed = subprocess.run(["cmd", "/d", "/c", str(script)], check=True, capture_output=True,
                                text=True, timeout=120).stdout
    env = {}
    for line in listed.splitlines():
        name, equals, value = line.partition("=")
        if name and equals:
            env[name] = value
    return env


@dataclass(frozen=True)
class Built:
    path: Path
    link: Path


def _cmake_list(values):
    def quoted(value):
        text = Path(value).as_posix() if isinstance(value, Path) else str(value)
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'
    return " ".join(quoted(v) for v in values)


def build(directory, name, sources, *, kind="executable", language="CXX", standard=17,
          defines=(), includes=(), plain_sources=(), pedantic=False, threads=False, timeout=600):
    directory = Path(directory).resolve()
    project, out = directory / f"{name}-project", directory / f"{name}-build"
    project.mkdir(parents=True, exist_ok=False)
    languages = "C CXX" if language == "C" or any(Path(s).suffix == ".c" for s in plain_sources) else "CXX"
    lines = [
        "cmake_minimum_required(VERSION 3.22)",
        f"project({name} LANGUAGES {languages})",
        f"set(CMAKE_{language}_STANDARD {standard})",
        f"set(CMAKE_{language}_STANDARD_REQUIRED ON)",
        f"set(CMAKE_{language}_EXTENSIONS OFF)",
        "set(CMAKE_WINDOWS_EXPORT_ALL_SYMBOLS ON)",
        f'set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "{out.as_posix()}/bin")',
        f'set(CMAKE_LIBRARY_OUTPUT_DIRECTORY "{out.as_posix()}/bin")',
        f'set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY "{out.as_posix()}/lib")',
    ]
    if kind == "executable":
        lines.append(f"add_executable({name} {_cmake_list(sources)})")
    elif kind == "shared":
        lines.append(f"add_library({name} SHARED {_cmake_list(sources)})")
    else:
        raise ValueError("kind is executable or shared")
    if plain_sources:
        lines += [f"add_library({name}_plain OBJECT {_cmake_list(plain_sources)})",
                  f"target_sources({name} PRIVATE $<TARGET_OBJECTS:{name}_plain>)"]
        if includes:
            lines.append(f"target_include_directories({name}_plain PRIVATE {_cmake_list(includes)})")
    if includes:
        lines.append(f"target_include_directories({name} PRIVATE {_cmake_list(includes)})")
    if defines:
        lines.append(f"target_compile_definitions({name} PRIVATE {_cmake_list(defines)})")
    lines += [
        "if(MSVC)",
        f"  target_compile_options({name} PRIVATE /O2 /W4 /WX /utf-8{' /permissive-' if pedantic and language == 'CXX' else ''})",
        "else()",
        f"  target_compile_options({name} PRIVATE -O2 -Wall -Wextra -Werror{' -pedantic' if pedantic else ''})",
        "endif()",
    ]
    if threads:
        lines += ["find_package(Threads REQUIRED)", f"target_link_libraries({name} PRIVATE Threads::Threads)"]
    if language == "C":
        lines += ["if(NOT WIN32)", f"  target_link_libraries({name} PRIVATE m)", "endif()"]
    (project / "CMakeLists.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    env = compiler_env()
    cmake = shutil.which("cmake", path=env.get("PATH") or env.get("Path"))
    if not cmake:
        raise RuntimeError("CMake is required to build a native probe")
    for command in ([cmake, "-G", "Ninja", "-S", str(project), "-B", str(out), "-DCMAKE_BUILD_TYPE="],
                    [cmake, "--build", str(out)]):
        done = subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout)
        if done.returncode != 0:
            raise RuntimeError(f"native probe {name} did not build:\n{done.stdout}\n{done.stderr}")
    return located(out, name, kind)


def located(out, name, kind):
    if os.name == "nt":
        if kind == "executable":
            path = out / "bin" / f"{name}.exe"
            return Built(path, path)
        return Built(out / "bin" / f"{name}.dll", out / "lib" / f"{name}.lib")
    if kind == "executable":
        path = out / "bin" / name
    else:
        path = out / "bin" / (f"lib{name}.dylib" if os.uname().sysname == "Darwin" else f"lib{name}.so")
    return Built(path, path)
