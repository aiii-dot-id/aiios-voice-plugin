def local_runtime():
    import onnxruntime as ort

    ort.disable_telemetry_events()
    return ort
