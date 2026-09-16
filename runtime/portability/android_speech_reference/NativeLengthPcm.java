package com.google.ai.edge.examples.asr;

import java.util.concurrent.atomic.AtomicLong;

/** Native PCM and generation fence. GPU graphs remain owned by public LiteRT
 * CompiledModel objects on the inference thread; no borrowed runtime handles.
 * close fences publication immediately, but never destroys an active graph. */
public final class NativeLengthPcm implements AutoCloseable {
    static { System.loadLibrary("voice_length_pcm"); }
    private final AtomicLong owner;
    public NativeLengthPcm(float[] mel) { owner = new AtomicLong(open(mel)); }
    private long handle() {
        long h = owner.get();
        if (h == 0) throw new IllegalStateException("native PCM owner retired");
        return h;
    }
    public void begin(long generation) { begin0(handle(), generation); }
    public void check(long generation) { check0(handle(), generation); }
    public void cancel(long generation) { cancel0(handle(), generation); }
    public float[] process(long generation, float[] pcm) { return process0(handle(), generation, pcm); }
    @Override public void close() { long h = owner.getAndSet(0); if (h != 0) retire(h); }
    private static native long open(float[] mel);
    private static native void begin0(long h, long generation);
    private static native void check0(long h, long generation);
    private static native void cancel0(long h, long generation);
    private static native float[] process0(long h, long generation, float[] pcm);
    private static native void retire(long h);
}
