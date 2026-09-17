"""Read actual loaded native images for a recorded desktop gate."""
import ctypes
import json
from pathlib import Path
import subprocess
from scripts.native_checkpoint_binding import sha

def process_children(parent,proc=Path('/proc')):
    # Linux records children on the spawning thread. A Go carrier can spawn
    # on any of its OS threads, not necessarily the thread whose TID is PID.
    tasks=proc/str(parent)/'task'
    children=set()
    for task in tasks.iterdir():
        try:children.update(int(n) for n in (task/'children').read_text().split())
        except FileNotFoundError:
            if task.exists():raise
    return sorted(children)

def observe_linux(parent,binary):
    children=process_children(parent)
    assert len(children)==1,children
    pid=int(children[0]);exe=Path(f'/proc/{pid}/exe').resolve()
    assert exe==(binary/'aii_voice_worker').resolve(),exe
    maps=Path(f'/proc/{pid}/maps').read_text()
    paths=sorted({line.split(None,5)[5] for line in maps.splitlines() if len(line.split(None,5))==6 and line.split(None,5)[5].startswith('/')})
    assert not any('python' in Path(p).name.lower() or 'torch' in Path(p).name.lower() for p in paths)
    names=('libaii_voice_runtime.so','libaii_native_asr.so','libaii_native_vad.so','libaii_native_endpoint.so','libnative_pocket_resident.so','libonnxruntime.so.1.24.2')
    owned={Path(p).name:{'path':p,'sha256':sha(Path(p))} for p in paths if Path(p).name in names}
    assert set(owned)==set(names),owned
    return {'pid':pid,'parent_pid':parent,'exe':str(exe),'images':paths,'native_images':owned,'maps':maps}

def observe_windows(parent, binary):
    """Read the actual child images, not merely the intended link inputs."""
    command=f"@(Get-CimInstance Win32_Process -Filter 'ParentProcessId = {int(parent)}' | Select-Object ProcessId,ExecutablePath) | ConvertTo-Json -Compress"
    children=json.loads(subprocess.check_output(['powershell.exe','-NoProfile','-Command',command],text=True,timeout=20))
    if isinstance(children,dict):children=[children]
    # The bound carrier opens by resolved file identity. Win32 may report
    # that executable with the extended-length prefix; spelling is not identity.
    assert len(children)==1 and Path(children[0]['ExecutablePath']).samefile(binary/'aii_voice_worker.exe'),children
    pid=children[0]['ProcessId'];k=ctypes.WinDLL('kernel32',use_last_error=True);p=ctypes.WinDLL('psapi',use_last_error=True)
    k.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong];k.OpenProcess.restype=ctypes.c_void_p
    k.CloseHandle.argtypes=[ctypes.c_void_p]
    p.EnumProcessModulesEx.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_ulong),ctypes.c_ulong]
    p.GetModuleFileNameExW.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_ulong]
    handle=k.OpenProcess(0x410,0,pid);assert handle,ctypes.get_last_error()
    try:
        modules=(ctypes.c_void_p*1024)();needed=ctypes.c_ulong()
        assert p.EnumProcessModulesEx(handle,modules,ctypes.sizeof(modules),ctypes.byref(needed),3) and needed.value<=ctypes.sizeof(modules)
        images=[]
        for mod in modules[:needed.value//ctypes.sizeof(ctypes.c_void_p)]:
            name=ctypes.create_unicode_buffer(32768);assert p.GetModuleFileNameExW(handle,mod,name,len(name))
            images.append(Path(name.value))
        assert not any('python' in x.name.lower() for x in images),'Python loaded in native worker'
        owned={x.name.lower():{'path':str(x),'sha256':sha(x)} for x in images if x.parent.samefile(binary)}
        expected=('aii_voice_runtime.dll','aii_native_asr.dll','aii_native_vad.dll','aii_native_endpoint.dll','native_pocket_resident.dll')
        assert all(n in owned for n in expected),owned
        return {'pid':pid,'parent_pid':parent,'images':[str(x) for x in images],'native_images':owned}
    finally:k.CloseHandle(handle)

def observe(parent,binary,platform,libraries):
    if platform=='windows':
        observe_worker = observe_windows
    else:
        observe_worker = observe_linux
    value=observe_worker(parent,binary)
    paths=value.get('images',[])
    if platform=='linux':paths=[x.split(None,5)[5] for x in value['maps'].splitlines() if len(x.split(None,5))==6 and x.split(None,5)[5].startswith('/')]
    actual={Path(p).name.lower():{'path':p,'sha256':sha(p)} for p in set(paths) if Path(p).name.lower() in libraries}
    suffix='.dll' if platform=='windows' else '.so'
    required={('' if platform=='windows' else 'lib')+n+suffix for n in ('aii_voice_runtime','aii_native_asr','aii_native_vad','aii_native_endpoint','aii_native_uid','aiii_uid_frontend','native_pocket_resident')}
    required.add('onnxruntime.dll' if platform=='windows' else 'libonnxruntime.so.1.24.2')
    assert required.issubset(actual),('required native image missing',required-set(actual))
    for name,row in actual.items():assert row['sha256']==libraries[name],('loaded image differs',name)
    value['bound_images']=actual
    return value
