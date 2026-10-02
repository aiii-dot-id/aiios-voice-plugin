package com.google.ai.edge.examples.asr;

/** Primitive equivalent of Kotlin maxByOrNull's Float ordering and first tie. */
public final class LogitArgmax {
    private LogitArgmax() {}

    public static int relative(float[] values, int start, int end) {
        if (start < 0 || end > values.length || start >= end) {
            throw new IllegalArgumentException("Nonempty in-bounds logit range required");
        }
        int best = start;
        float value = values[start];
        for (int i = start + 1; i < end; i++) {
            // Float.compare, not >: match NaNs, signed zero and the first tie.
            if (Float.compare(values[i], value) > 0) {
                value = values[i];
                best = i;
            }
        }
        return best - start;
    }

    public static boolean identical(float[] a, float[] b) {
        if (a.length != b.length) return false;
        for (int i = 0; i < a.length; i++) {
            if (Float.floatToRawIntBits(a[i]) != Float.floatToRawIntBits(b[i])) return false;
        }
        return true;
    }
}
