"""Drive the actual Go SDK carrier, with separate control/audio pipes.

Recorded input and sink delivery evidence only. No browser, device, acoustic
stop, AII OS conversation or human-quality qualification is claimed here.
"""

import argparse
import hashlib
import json
import os
import queue
import re
import struct
import subprocess
import sys
import threading
import time
import wave
import zipfile
from pathlib import Path

import numpy as np

from runtime.plugin_engine.audio import END, PCM, Frame, exact, read_frame
from scripts.build_plugin_carrier import (
    BUILD_DIR,
    SDK_SOURCE,
    carrier_path,
    verify_build,
)

ROOT = Path(__file__).resolve().parents[1]


def capture_source_archive(output, paths, *, root, carrier):
    """Name an external runtime entrypoint without making sources unbounded."""
    named = {}
    for path in paths:
        try:
            name = path.relative_to(root).as_posix()
        except ValueError:
            if path.resolve() != carrier.resolve():
                raise ValueError(
                    "only the bound carrier may be outside the source root"
                ) from None
            name = "__runtime_artifact__/" + path.name
        if name in named:
            raise ValueError("duplicate source archive name: " + name)
        named[name] = path
    hashes = {}
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
        for name, path in named.items():
            data = path.read_bytes()
            hashes[name] = hashlib.sha256(data).hexdigest()
            archive.writestr(name, data)
    return hashes


def describe_carrier(carrier):
    """Check the executable's declared wire before creating audio/model workers."""
    if Path(carrier).resolve().parent == BUILD_DIR.resolve():
        verify_build()
    result = subprocess.run(
        [str(carrier)],
        env={**os.environ, "AIISDK_DESCRIBE": "1"},
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    description = json.loads(result.stdout)
    validate_description(description)
    if "AII_VOICE_READY" in result.stderr:
        raise ValueError("Describe loaded a model worker")
    return {
        "operations": description,
        "carrier_sha256": hashlib.sha256(Path(carrier).read_bytes()).hexdigest(),
    }


def validate_description(description):
    expected = {
        "speech.session." + name
        for name in (
            "open",
            "synthesize",
            "cancel_synthesis",
            "stop_playback",
            "finish_input",
            "close",
            "status",
            "playback_report",
        )
    }
    if not isinstance(description, list) or any(
        not isinstance(row, dict) for row in description
    ):
        raise ValueError("Invalid resident SDK declaration")
    ids = [row.get("id") for row in description]
    # Retain frozen checkpoint coverage and explicitly admit the fifth guided
    # capture operation, not arbitrary extra callable shapes.
    enrollment = {"speaker." + name for name in ("list", "enroll", "remove", "reset")}
    if "speaker.discard_capture" in ids:
        enrollment.add("speaker.discard_capture")
    if "speaker.upgrade_policy" in ids:
        enrollment.add("speaker.upgrade_policy")
    with_enrollment = set(ids) == expected | enrollment
    if (
        any(not isinstance(x, str) for x in ids)
        or (set(ids) != expected and not with_enrollment)
        or len(ids) != (8 + len(enrollment) if with_enrollment else 8)
    ):
        raise ValueError("The resident SDK must declare eight controls and only the known enrollment operations")
    if with_enrollment:
        for row in description:
            if row["id"] in enrollment:
                write = row["id"] != "speaker.list"
                if bool(row.get("operator_confirms", False)) != write or row.get("capabilities") != ["fs.private"] or row.get("effects") != ("write.local" if write else "read.internal"):
                    raise ValueError("Enrollment effects/confirmation contract differs")


class SDKHost:
    def __init__(self, args, *, worker_command=None):
        # Until construction returns the caller cannot own this host. Register
        # every resource immediately, including raw descriptors not yet wrapped.
        self.process = None
        self.to_engine = self.from_engine = self.log = None
        self.threads = []
        self._construction_fds = set()
        try:
            self._initialize(args, worker_command=worker_command)
        except BaseException as error:
            try:
                self.close()
            except Exception as cleanup_error:
                raise RuntimeError(
                    f"SDKHost construction failed; cleanup incomplete: {cleanup_error}"
                ) from error
            raise

    def _initialize(self, args, *, worker_command=None):
        # Explicit proof composition only. A packaged carrier must always pick
        # its own verified entrypoint, even when driven by a diagnostic host.
        if worker_command is not None and (
            getattr(args, "packaged_runtime", False)
            or args.fixture
            or not isinstance(worker_command, (list, tuple))
            or not worker_command
            or any(not isinstance(x, str) or not x for x in worker_command)
        ):
            raise ValueError(
                "explicit real-worker command requires an unpackaged proof"
            )
        from runtime.plugin_engine.worker import native_candidate_options

        native = native_candidate_options(args)
        self.description = describe_carrier(args.carrier)
        self.events, self.frames, self.calls = [], [], []
        self.responses = queue.Queue()
        self.host_requests = queue.Queue(maxsize=16)
        self.errors = queue.Queue()
        self.counter = 0
        self.write_lock = threading.Lock()
        self.operator_settings = getattr(args, "operator_settings", None)
        self.extra_host_operations = frozenset(getattr(args, "extra_host_operations", ()))
        self.started = time.perf_counter()
        self.startup_timing = {"host_runtime_verification_seconds": 0.0}
        i_read, i_write = os.pipe()
        self._construction_fds.update((i_read, i_write))
        o_read, o_write = os.pipe()
        self._construction_fds.update((o_read, o_write))
        self.to_engine = os.fdopen(i_write, "wb", buffering=0)
        self._construction_fds.remove(i_write)
        self.from_engine = os.fdopen(o_read, "rb", buffering=0)
        self._construction_fds.remove(o_read)
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                filter(None, (str(ROOT), os.environ.get("PYTHONPATH")))
            ),
        }
        if os.name == "nt":
            import msvcrt

            handles = [msvcrt.get_osfhandle(fd) for fd in (i_read, o_write)]
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            startup = subprocess.STARTUPINFO()
            startup.lpAttributeList = {"handle_list": handles}
            creation = {"startupinfo": startup, "close_fds": True}
        else:
            handles = (i_read, o_write)
            creation = {"pass_fds": handles}
        env.update(AII_AUDIO_IN_FD=str(handles[0]), AII_AUDIO_OUT_FD=str(handles[1]))
        # A Windows venv launcher is an additional parent process, not the model
        # worker. Use the actual interpreter and explicit dependency paths.
        python = sys._base_executable if os.name == "nt" else sys.executable
        command = [str(args.carrier), python, "-m"]
        if args.fixture:
            command += [getattr(args, "fixture_module", "tests.plugin_worker_fixture")]
        else:
            command += ["runtime.plugin_engine.worker", "--root", str(ROOT)]
            backend = getattr(args, "backend", "mlx")
            command += ["--backend", backend]
            if backend != "mlx":
                command += [
                    "--stage",
                    str(args.stage),
                    "--state-dir",
                    str(args.output / "engine-state"),
                ]
            if backend == "windows-pocket":
                command += ["--pocket-root", str(args.pocket_root)]
                for key, value in native.items():
                    command += ["--" + key.replace("_", "-"), str(value)]
        if worker_command is not None:
            command = [str(args.carrier), *worker_command]
        self.log = (args.output / "worker.stderr.log").open("wb")
        if getattr(args, "packaged_runtime", False):
            if args.fixture:
                raise ValueError("packaged proof requires real models")
            from scripts.package_native_runtime import verify

            verify_begin = time.perf_counter()
            verify(Path(args.carrier).resolve().parent, args.runtime_manifest_sha)
            self.startup_timing["host_runtime_verification_seconds"] = (
                time.perf_counter() - verify_begin
            )
            command = [str(args.carrier)]
            env["AII_MODELS_DIR"] = str(getattr(args, "model_data_root", None) or ROOT)
        if getattr(args, "sandbox_profile", None):
            command = [
                "/usr/bin/sandbox-exec",
                "-f",
                str(args.sandbox_profile),
                *command,
            ]
        self.launch_command = command
        self.startup_timing["carrier_spawn_begin_elapsed"] = (
            time.perf_counter() - self.started
        )
        self.process = subprocess.Popen(
            command,
            cwd=Path(args.carrier).resolve().parent
            if getattr(args, "packaged_runtime", False)
            else ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            **creation,
        )
        self.startup_timing["carrier_spawn_return_elapsed"] = (
            time.perf_counter() - self.started
        )
        os.close(i_read)
        self._construction_fds.remove(i_read)
        os.close(o_write)
        self._construction_fds.remove(o_write)
        for target in (self.control_reader, self.audio_reader):
            t = threading.Thread(target=target, daemon=True)
            self.threads.append(t)
            t.start()

    def control_reader(self):
        try:
            while (head := exact(self.process.stdout, 4, boundary=True)) is not None:
                size = struct.unpack(">I", head)[0]
                if not 0 < size <= 1024 * 1024:
                    raise ValueError("invalid SDK control length")
                body = json.loads(exact(self.process.stdout, size))
                if body.get("method") == "session.event":
                    if "id" in body:
                        raise ValueError("notification carried an RPC ID")
                    self.events.append(
                        {
                            "elapsed": time.perf_counter() - self.started,
                            **body["params"],
                        }
                    )
                elif body.get("method") == "invoke.call" and "id" in body:
                    # Tests answer upstream calls separately from downstream
                    # control replies; their numeric IDs may legitimately match.
                    if body["params"].get("operation") in self.extra_host_operations:
                        self.host_requests.put_nowait(body)
                    elif self.operator_settings is not None:
                        if body["params"] != {
                            "operation": "settings.get",
                            "arguments": {},
                        }:
                            raise ValueError(
                                "unexpected upstream call in settings proof"
                            )
                        reply = {
                            "jsonrpc": "2.0",
                            "id": body["id"],
                            "result": {
                                "status": "succeeded",
                                "operation_result": {"values": self.operator_settings},
                            },
                        }
                        raw = json.dumps(reply, allow_nan=False).encode()
                        with self.write_lock:
                            self.process.stdin.write(struct.pack(">I", len(raw)) + raw)
                            self.process.stdin.flush()
                    else:
                        self.host_requests.put_nowait(body)
                elif "id" in body and "method" not in body:
                    self.responses.put(body)
                else:
                    raise ValueError("unclassified SDK output")
        except (OSError, EOFError, ValueError, RuntimeError, queue.Full) as error:
            self.errors.put(repr(error))

    def audio_reader(self):
        try:
            while (frame := read_frame(self.from_engine)) is not None:
                self.frames.append((time.perf_counter() - self.started, frame))
        except (OSError, EOFError, ValueError, RuntimeError) as error:
            self.errors.put(repr(error))

    def call(self, operation, args, timeout=10):
        self.counter += 1
        request = {
            "jsonrpc": "2.0",
            "id": self.counter,
            "method": "invoke.call",
            "params": {"operation": "speech.session." + operation, "arguments": args},
        }
        data = json.dumps(request, allow_nan=False).encode()
        begun = time.perf_counter()
        with self.write_lock:
            self.process.stdin.write(struct.pack(">I", len(data)) + data)
            self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                reply = self.responses.get(
                    timeout=min(0.1, max(0.001, deadline - time.monotonic()))
                )
                break
            except queue.Empty:
                if self.process.poll() is not None:
                    raise RuntimeError(
                        "carrier exited while awaiting admission; inspect retained stderr"
                    ) from None
                if time.monotonic() >= deadline:
                    raise TimeoutError("SDK admission " + operation) from None
        self.calls.append(
            {
                "operation": operation,
                "arguments": args,
                "seconds": time.perf_counter() - begun,
                "reply": reply,
            }
        )
        if reply["id"] != self.counter:
            raise ValueError("reply identity mismatch")
        if "error" in reply:
            raise ValueError(reply["error"])
        return reply["result"]

    def event(self, kind, sid=None, timeout=15, *, session_id=None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.events:
                if session_id is not None and event.get("session_id") != session_id:
                    continue
                if event["type"] == "failure":
                    raise RuntimeError(
                        "engine failure: " + event.get("reason", "unknown")
                    )
                if event["type"] == kind and (
                    sid is None or event.get("synthesis_id") == sid
                ):
                    return event
            if not self.errors.empty():
                raise RuntimeError(self.errors.get())
            if self.process.poll() is not None:
                raise RuntimeError("carrier ended before " + kind)
            time.sleep(0.005)
        raise TimeoutError(kind)

    def readiness(self, timeout=180):
        """Observe the actual SDK stderr line before sending any admission."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = Path(self.log.name).read_text(encoding="utf8", errors="replace")
            lines = re.findall(
                r"^AII_VOICE_READY event=ready models_loaded=(\d+) accelerator=(\w+) probe_ms=(\d+)$",
                data,
                re.MULTILINE,
            )
            if lines:
                if len(lines) != 1:
                    raise ValueError("duplicate public SDK readiness")
                models, accelerator, probe = lines[0]
                observed = time.perf_counter() - self.started
                return {
                    "models_loaded": int(models),
                    "accelerator": accelerator,
                    "probe_ms": int(probe),
                    "observed_elapsed": observed,
                    "host_startup_timing": {
                        **self.startup_timing,
                        "spawn_to_ready_seconds": observed
                        - self.startup_timing["carrier_spawn_begin_elapsed"],
                        "scope": "spawn-to-ready includes carrier integrity verification, interpreter startup, model loading and warm-up",
                    },
                }
            if self.process.poll() is not None:
                raise RuntimeError("carrier ended before SDK readiness")
            time.sleep(0.01)
        raise TimeoutError("SDK readiness")

    def first_pcm(self, stream, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for elapsed, frame in self.frames:
                if frame.stream == stream and frame.kind == PCM:
                    return elapsed
            if any(e["type"] == "failure" for e in self.events):
                raise RuntimeError("engine failed before first PCM")
            if self.process.poll() is not None:
                raise RuntimeError("carrier ended before first PCM")
            time.sleep(0.005)
        raise TimeoutError("first PCM")

    def close(self):
        # Also valid after partial construction. One failed close must not skip
        # child retirement or the remaining resources, and an unresolved owner
        # must not be reported as a clean qualification shutdown.
        failures = []

        def attempt(label, fn):
            try:
                return fn()
            except Exception as error:
                failures.append(f"{label}: {error!r}")

        def close_stream(label, stream):
            if stream is not None and not stream.closed:
                attempt(label, stream.close)

        close_stream("control input", self.process.stdin if self.process else None)
        close_stream("audio input", self.to_engine)
        for fd in tuple(self._construction_fds):
            try:
                os.close(fd)
            except OSError as error:
                failures.append(f"untransferred descriptor {fd}: {error!r}")
            else:
                self._construction_fds.remove(fd)
        code = None
        if self.process is not None:
            def retire():
                try:
                    return self.process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    return self.process.wait(timeout=3)
            code = attempt("child retirement", retire)
        for t in self.threads:
            if t.ident is not None:
                attempt("reader join", lambda: t.join(timeout=1))
                if t.is_alive():
                    failures.append("reader did not retire")
        # A buffered pipe's close can wait behind its active reader. If a
        # process/reader has not retired, return an explicit fault, not a new
        # unbounded wait on its I/O lock. The caller retains this host to retry.
        retired = self.process is None or self.process.poll() is not None
        if retired and not any(t.is_alive() for t in self.threads):
            close_stream("audio output", self.from_engine)
            if self.process is not None:
                close_stream("control output", self.process.stdout)
                close_stream("child error pipe", self.process.stderr)
        close_stream("diagnostic log", self.log)
        if failures:
            raise RuntimeError("SDKHost cleanup incomplete: " + "; ".join(failures))
        return code


def run(
    args,
    *,
    host=None,
    session_id="sdk-engine-proof",
    synthesis_prefix="",
    close_host=True,
):
    args.output.mkdir(parents=True, exist_ok=False)
    sdk_source = getattr(args, "sdk_source", None) or SDK_SOURCE
    carrier_source = getattr(args, "carrier_source", None) or ROOT / "plugin/native"
    files = [
        # Bind reused recognizer, endpoint, output service and model verification
        # code as well as the new carrier; hashes of just the adapter are not
        # enough to reproduce the implementation that actually ran.
        *sorted((ROOT / "runtime").rglob("*.py")),
        *sorted((ROOT / "scripts").glob("*.py")),
        *sorted((sdk_source / "pkg").rglob("*.go")),
        sdk_source / "go.mod",
        *sorted((ROOT / "research/acquisition").glob("*.json")),
        *sorted(carrier_source.glob("*.go")),
        carrier_source / "go.mod",
        args.carrier,
        ROOT / "tests/plugin_worker_fixture.py",
        ROOT / "tests/plugin_models.py",
        ROOT / "tests/test_plugin_engine.py",
        ROOT / "plugin/runtime_bootstrap.py",
    ]
    report = {
        "passed": False,
        "scope": "actual Go SDK + engine worker; recorded input and PCM delivery only",
        "fixture_models": args.fixture,
        "backend": "fixture" if args.fixture else getattr(args, "backend", "mlx"),
        "interruption_mode": "recorded_speech_vad"
        if args.spoken_interrupt
        else "controls",
        "playback_reports": "simulated_client"
        if getattr(args, "playback_reports", False)
        else "none",
        "source_sha256": capture_source_archive(
            args.output / "executed-source.zip", files, root=ROOT, carrier=args.carrier
        ),
    }
    host = host or SDKHost(args)
    if getattr(args, "packaged_runtime", False):
        report["packaged_runtime"] = {
            "manifest_sha256": args.runtime_manifest_sha,
            "command": host.launch_command,
            "data_only_root": str(getattr(args, "model_data_root", None) or ROOT),
            "sandbox_profile": str(getattr(args, "sandbox_profile", "")),
        }
    report["sdk_description"] = host.description
    event_start, frame_start, call_start = (
        len(host.events),
        len(host.frames),
        len(host.calls),
    )
    first_id, interrupt_id, recovery_id = (
        synthesis_prefix + x for x in ("s1", "s2", "s3")
    )
    first_text = getattr(args, "first_text", "The local voice service is ready.")
    recovery_text = getattr(
        args,
        "recovery_text",
        "The recovery reply is complete. Cobalt lantern seventeen.",
    )
    report["requested_speech"] = {first_id: first_text, recovery_id: recovery_text}
    try:
        if args.fixture:
            samples = np.full(1037, 12000, dtype="<i2")
        else:
            from scripts.audio_contract import normalize_pcm_wav

            path = Path(getattr(args, "recorded_input", None) or
                ROOT / "deliverables/speech-output/validation-20260907-r2/recovery.wav")
            samples = np.rint(
                np.clip(normalize_pcm_wav(path, 16000).samples, -1, 32767 / 32768)
                * 32768
            ).astype("<i2")
            report["input"] = {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "samples": len(samples),
            }
        sid = session_id
        opened = host.call(
            "open",
            {
                "session_id": sid,
                "input_handle": "mic",
                "output_handle": "speaker",
                "audio": {
                    "format": "s16le",
                    "input": {"rate": 48000, "channels": 1},
                    "output": {"rate": 48000, "channels": 2},
                },
            },
            timeout=180,
        )
        assert opened["audio"] == {
            "input": {"rate": 16000, "channels": 1},
            "output": {"rate": 24000, "channels": 1},
        }
        host.event("session_ready", session_id=sid)
        host.call(
            "synthesize",
            {
                "session_id": sid,
                "synthesis_id": first_id,
                "text": first_text,
            },
        )
        first = host.event("synthesis_end", first_id, timeout=90, session_id=sid)
        assert first["delivered_samples"] > 0 and first["playback_verified"] is False
        if getattr(args, "playback_reports", False):
            from scripts.plugin_receipt_probe import receipt

            report["receipts"] = [receipt(host, sid, first, retry=True)]
        # A second distinct host reply can be cancelled independently.
        second = host.call(
            "synthesize",
            {
                "session_id": sid,
                "synthesis_id": interrupt_id,
                "text": "This is a longer reply. Please interrupt this sentence and keep the opening words in the next turn. "
                * (8 if args.spoken_interrupt else 2),
            },
        )
        if args.spoken_interrupt:
            report["interrupt_first_pcm"] = host.first_pcm(second["output_stream"])
        else:
            host.call(
                "stop_playback", {"session_id": sid, "synthesis_id": interrupt_id}
            )
            host.call(
                "cancel_synthesis", {"session_id": sid, "synthesis_id": interrupt_id}
            )
        host.call("status", {"session_id": sid})
        if not args.spoken_interrupt:
            cancelled = host.event(
                "synthesis_cancelled", interrupt_id, timeout=30, session_id=sid
            )
        report["input_feed_begin_elapsed"] = time.perf_counter() - host.started
        # Finish is intentionally admitted before the tail crosses the audio pipe.
        for seq, start in enumerate(range(0, len(samples), 512), 1):
            if start + 512 >= len(samples):
                host.call(
                    "finish_input",
                    {"session_id": sid, "stream_id": "mic", "end_sample": len(samples)},
                )
            frame = Frame(PCM, 7, seq, start, samples[start : start + 512].tobytes())
            host.to_engine.write(frame.encode())
            if not args.fixture:
                time.sleep(512 / 16000)
        host.to_engine.write(Frame(END, 7, seq + 1, len(samples)).encode())
        if args.spoken_interrupt:
            host.event("interruption_requested", interrupt_id, session_id=sid)
            cancelled = host.event(
                "synthesis_cancelled", interrupt_id, timeout=30, session_id=sid
            )
            assert cancelled["delivered_samples"] > 0
        final = host.event("transcript_final", timeout=60, session_id=sid)
        report["transcript"] = final["text"]
        expected = (
            "cobalt lantern seventeen"
            if args.fixture
            else "Please keep the opening words cobalt lantern seventeen. "
            "The recovery reply is now complete."
        )
        report["expected_transcript"] = expected
        assert re.findall(r"\w+", final["text"].lower()) == re.findall(
            r"\w+", expected.lower()
        ), "opening or later words changed"
        completed = host.event("input_finished", timeout=60, session_id=sid)
        assert (
            completed["end_sample"] == completed["processed_end_sample"] == len(samples)
        )
        assert completed["stream_id"] == "mic"
        assert completed["sequence"] > final["sequence"]
        if getattr(args, "playback_reports", False):
            report["receipts"].append(
                receipt(host, sid, cancelled, stopped=True, retry=True)
            )
        host.call(
            "synthesize",
            {
                "session_id": sid,
                "synthesis_id": recovery_id,
                "text": recovery_text,
            },
        )
        last = host.event("synthesis_end", recovery_id, timeout=90, session_id=sid)
        assert last["delivered_samples"] > 0
        snapshot = host.call("status", {"session_id": sid})
        report["final_snapshot"] = snapshot
        assert snapshot["input_completion"] == {
            k: completed[k]
            for k in ("stream_id", "end_sample", "processed_end_sample", "sequence")
        }
        assert snapshot["playback"]["state"] == "unobserved"
        if getattr(args, "playback_reports", False):
            host.call("close", {"session_id": sid, "mode": "drain"})
            waiting = host.call("status", {"session_id": sid})
            assert waiting["lifecycle"] == "draining"
            assert waiting["playback"]["queued_samples"] == last["delivered_samples"]
            assert not any(
                e["type"] == "session_end" for e in host.events[event_start:]
            )
            report["waiting_for_final_receipt"] = waiting
            report["receipts"].append(receipt(host, sid, last))
            terminal = host.event("session_end", session_id=sid)
            assert terminal["status"] == "completed"
            report["close_mode"] = "drain_with_simulated_client_reports"
        else:
            # Without client reports, explicitly abort engine resources;
            # this path never labels pipe delivery a successful playback drain.
            host.call("close", {"session_id": sid, "mode": "abort"})
            host.event("session_end", session_id=sid)
            report["close_mode"] = "abort_without_playback_reports"
        assert [e["sequence"] for e in host.events[event_start:]] == list(
            range(1, len(host.events) - event_start + 1)
        )
        report["passed"] = True
    except (
        OSError,
        EOFError,
        ValueError,
        RuntimeError,
        TimeoutError,
        queue.Empty,
        AssertionError,
        KeyboardInterrupt,
    ) as error:
        report["error"] = repr(error)
    finally:
        report["exit_code"] = host.close() if close_host else None
        report["passed"] = (
            report["passed"]
            and (not close_host or report["exit_code"] == 0)
            and host.errors.empty()
        )
        report["session_id"] = session_id
        report["events"], report["calls"] = (
            host.events[event_start:],
            host.calls[call_start:],
        )
        events = report["events"]
        # Recheck after both pipes have closed, including any last queued event.
        if [e["sequence"] for e in events] != list(range(1, len(events) + 1)):
            report["passed"] = False
            report["event_error"] = "noncontiguous terminal event history"
        if not events or events[-1]["type"] != "session_end":
            report["passed"] = False
            report["event_error"] = "missing or post-terminal session events"
        report["frame_spans"] = [
            {
                "elapsed": elapsed,
                "kind": f.kind,
                "stream": f.stream,
                "seq": f.seq,
                "start": f.start,
                "samples": len(f.pcm) // 2,
            }
            for elapsed, f in host.frames[frame_start:]
        ]
        for stream in sorted({f.stream for _, f in host.frames[frame_start:]}):
            frames = [f for _, f in host.frames[frame_start:] if f.stream == stream]
            pcm = b"".join(f.pcm for f in frames if f.kind == PCM)
            end = next((f for f in frames if f.kind == END), None)
            position = 0
            for seq, frame in enumerate(frames):
                if (
                    frame.seq != seq
                    or frame.start != position
                    or frame.kind not in (PCM, END)
                    or frame.kind == END
                    and seq != len(frames) - 1
                ):
                    report["passed"] = False
                    report["frame_error"] = "noncontiguous or post-terminal output"
                position += len(frame.pcm) // 2
            if end is None or end.start != len(pcm) // 2:
                report["passed"] = False
                report["frame_error"] = "missing or incorrect output end"
            with wave.open(str(args.output / f"output-stream-{stream}.wav"), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(pcm)
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: report.get(k)
                for k in (
                    "passed",
                    "fixture_models",
                    "transcript",
                    "error",
                    "exit_code",
                    "frame_error",
                )
            }
        )
    )
    return report["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--spoken-interrupt", action="store_true")
    parser.add_argument(
        "--playback-reports",
        action="store_true",
        help="test-only simulated client receipts; not browser rendering",
    )
    parser.add_argument("--carrier", type=Path, default=carrier_path())
    parser.add_argument("--sdk-source", type=Path)
    parser.add_argument("--carrier-source", type=Path)
    parser.add_argument(
        "--backend", choices=("mlx", "cuda", "windows-pocket"), default="mlx"
    )
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--pocket-root", type=Path)
    parser.add_argument("--native-stt-root", type=Path)
    parser.add_argument("--native-stt-python", type=Path)
    raise SystemExit(0 if run(parser.parse_args()) else 1)
