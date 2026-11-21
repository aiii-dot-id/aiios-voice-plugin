import os
import subprocess
import sys


def test_worker_retires_during_model_load_when_carrier_disappears():
    if os.name == "nt":
        import threading

        from runtime.plugin_engine.worker import watch_carrier_liveness

        before = threading.active_count()
        os.environ["AII_VOICE_CARRIER_LIVENESS_FD"] = "not-a-descriptor"
        try:
            watch_carrier_liveness()
        finally:
            del os.environ["AII_VOICE_CARRIER_LIVENESS_FD"]
        assert threading.active_count() == before
        return
    read_fd, write_fd = os.pipe()
    env = {**os.environ, "AII_VOICE_CARRIER_LIVENESS_FD": str(read_fd)}
    child = subprocess.Popen(
        [sys.executable, "-c", (
            "from runtime.plugin_engine.worker import watch_carrier_liveness; "
            "watch_carrier_liveness(); "
            "import time; time.sleep(3)"
        )],
        env=env,
        pass_fds=(read_fd,),
    )
    os.close(read_fd)
    os.close(write_fd)
    try:
        assert child.wait(timeout=2) == 74
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
