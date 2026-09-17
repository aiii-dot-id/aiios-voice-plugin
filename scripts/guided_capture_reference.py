"""Independent native UID inference and strict persistence comparison.

The same-platform ABI result tests storage fidelity at 1e-12. Cross-platform
model parity uses the already frozen UID vector gate (1e-4 / cosine .9999),
not a tolerance inferred from a failed capture. Neither changes UID policy.
"""
import ctypes as C
import math
import os
from pathlib import Path


def library_member(files, platform):
    suffix = {'windows': '.dll', 'linux': '.so', 'darwin': '.dylib', 'macos': '.dylib'}[platform]
    name = ('' if platform == 'windows' else 'lib') + 'aii_native_uid' + suffix
    matches = [member for member in files if Path(member).name == name]
    if len(matches) != 1:
        raise ValueError('runtime must bind exactly one native UID inference library')
    return matches[0]


def unit(vector):
    if len(vector) != 256 or not all(math.isfinite(x) for x in vector):
        raise ValueError('UID vector must have 256 finite coordinates')
    norm = sum(x*x for x in vector)**.5
    if not math.isfinite(norm) or norm < 1e-12:
        raise ValueError('invalid UID vector norm')
    return [x/norm for x in vector]


def compare(persisted, direct, cross_platform):
    # Do not normalize the stored vector: doing so would conceal storage damage.
    unit(persisted)
    direct, cross_platform = unit(direct), unit(cross_platform)
    exact = max(abs(x-y) for x,y in zip(persisted, direct))
    delta = max(abs(x-y) for x,y in zip(direct, cross_platform))
    cosine = sum(x*y for x,y in zip(direct, cross_platform))
    if exact >= 1e-12:
        raise ValueError(f'persisted vector differs from independent local ABI: {exact}')
    if delta > .0001 or cosine < .9999:
        raise ValueError(f'frozen cross-platform UID parity failed: {delta}, {cosine}')
    return {'persisted_max_absolute_error': exact,
            'cross_platform_max_absolute_error': delta,
            'cross_platform_cosine': cosine,
            'persistence_ceiling': 1e-12,
            'cross_platform_ceiling': .0001,
            'cross_platform_cosine_floor': .9999}


def embed(library, model, pcm):
    library, model = Path(library).resolve(), Path(model).resolve()
    if not pcm or len(pcm) % 2:
        raise ValueError('expected nonempty 16 kHz mono s16le recording')
    search = os.add_dll_directory(str(library.parent)) if os.name == 'nt' else None
    try:
        lib = C.CDLL(str(library))
        lib.aii_uid_create.argtypes = [C.c_void_p, C.c_size_t, C.c_char_p, C.c_void_p, C.c_size_t]
        lib.aii_uid_create.restype = C.c_void_p
        lib.aii_uid_embed.argtypes = [C.c_void_p, C.c_uint64, C.c_void_p, C.c_size_t,
                                    C.c_int, C.POINTER(C.c_double), C.c_size_t,
                                    C.c_void_p, C.c_size_t]
        lib.aii_uid_embed.restype = C.c_int
        lib.aii_uid_destroy.argtypes = [C.c_void_p]
        lib.aii_uid_destroy.restype = None
        raw = model.read_bytes()
        error = C.create_string_buffer(2048)
        handle = lib.aii_uid_create(raw, len(raw), b'cpu', error, len(error))
        if not handle:
            raise RuntimeError(error.value.decode(errors='replace'))
        try:
            values = (C.c_double * 256)()
            code = lib.aii_uid_embed(handle, 1, bytes(pcm), len(pcm), 16000,
                                     values, 256, error, len(error))
            if code:
                raise RuntimeError(error.value.decode(errors='replace'))
            return unit(list(values))
        finally:
            lib.aii_uid_destroy(handle)
    finally:
        if search:
            search.close()
