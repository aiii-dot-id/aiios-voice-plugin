"""Process-local ONNX initialization for the offline voice engine."""


def local_runtime():
    # Import only when a model is constructed, not when a transport or test
    # imports its adapter. Native optional telemetry has no role in local
    # speech. Disable it before creating any ONNX model session.
    import onnxruntime as ort

    ort.disable_telemetry_events()
    return ort
