import numpy as np


def normalized_sources(mixture, sources):
    x = np.asarray(mixture, dtype=np.float64)
    if (x.ndim != 1 or not x.size or x.size > 480000 or
            not np.isfinite(x).all() or np.max(np.abs(x)) > 1):
        raise ValueError('invalid mixture PCM')
    if len(sources) != 2:
        raise ValueError('two separator outputs required')
    checked = []
    for source in sources:
        y = np.asarray(source, dtype=np.float64)
        if y.shape != x.shape or not np.isfinite(y).all():
            raise ValueError('invalid separated waveform extent or values')
        checked.append(y)
    rms_input = float(np.sqrt(np.mean(x*x)))
    result = []
    for y in checked:
        peak = float(np.max(np.abs(y)))
        if peak == 0 or rms_input == 0:
            result.append(np.zeros(x.shape, dtype=np.float32))
            continue
        scaled = y/peak
        rms = float(np.sqrt(np.mean(scaled*scaled)))
        gain = min(rms_input/rms, .95)
        result.append((scaled*gain).astype(np.float32))
    return result


def pcm16(pcm):
    x = np.asarray(pcm, dtype=np.float64)
    if x.ndim != 1 or not x.size or x.size > 480000 or not np.isfinite(x).all() or np.max(np.abs(x)) > 1:
        raise ValueError('UID input must be normalized finite mono PCM')
    return np.minimum(np.rint(x*32768), 32767).astype('<i2').tobytes()
