package com.google.ai.edge.examples.asr;

import com.google.ai.edge.litert.TensorBuffer;

final class NativeLogits {
    static { System.loadLibrary("voice_logit_window"); }
    private NativeLogits() {}
    static native float[] readWindow(TensorBuffer buffer, int offset, int count, int expectedElements);
}
