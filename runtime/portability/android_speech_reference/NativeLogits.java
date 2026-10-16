package com.google.ai.edge.examples.asr;

import com.google.ai.edge.litert.TensorBuffer;

/** Private, version-bound experiment. Never a public SDK handle contract. */
final class NativeLogits {
    static { System.loadLibrary("voice_logit_window"); }
    private NativeLogits() {}
    static native float[] readWindow(TensorBuffer buffer, int offset, int count, int expectedElements);
}
