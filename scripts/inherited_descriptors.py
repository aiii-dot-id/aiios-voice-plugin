import os
import subprocess


def inherit(*fds):
    if os.name != "nt":
        return [str(fd) for fd in fds], {"pass_fds": fds}
    import msvcrt

    handles = [msvcrt.get_osfhandle(fd) for fd in fds]
    for handle in handles:
        os.set_handle_inheritable(handle, True)
    startup = subprocess.STARTUPINFO()
    startup.lpAttributeList = {"handle_list": handles}
    return [str(handle) for handle in handles], {"startupinfo": startup, "close_fds": True}
