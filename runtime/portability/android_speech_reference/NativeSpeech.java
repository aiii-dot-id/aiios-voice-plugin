package com.google.ai.edge.examples.asr;
import java.util.concurrent.atomic.AtomicLong;

/** Test adapter for the project-owned native recognizer, not a LiteRT handle. */
public final class NativeSpeech implements AutoCloseable {
    static { System.loadLibrary("voice_native_tdt"); }
    private final AtomicLong owner;
    private final AtomicLong generation = new AtomicLong();
    public NativeSpeech(String model, String dispatcher) { owner = new AtomicLong(open(model, dispatcher)); }
    public String recognize(float[] features) { return recognize0(owner.get(), generation.incrementAndGet(), features, true, 0); }
    public String recognize(long id, float[] features, boolean capture, int holdAfter) {
        return recognize0(owner.get(), id, features, capture, holdAfter);
    }
    public float[] trace() { return trace0(owner.get()); }
    public String pcm(long id, float[] samples) { return pcm0(owner.get(), id, samples); }
    public String sequence(long id, float[] samples, int[] packets, int holdWindow) { return sequence0(owner.get(), id, samples, packets, holdWindow); }
    public static float[] frontend(float[] samples) { return frontend0(samples); }
    public void cancel(long through) { cancel0(owner.get(), through); }
    public void release(long id) { release0(owner.get(), id); }
    public String status() { return status0(owner.get()); }
    public void close() { long id = owner.getAndSet(0); if (id != 0) retire(id); }
    private static native long open(String model, String dispatcher);
    private static native String recognize0(long owner, long id, float[] features, boolean capture, int holdAfter);
    private static native float[] trace0(long owner);
    private static native String pcm0(long owner, long id, float[] samples);
    private static native String sequence0(long owner, long id, float[] samples, int[] packets, int holdWindow);
    private static native float[] frontend0(float[] samples);
    private static native void cancel0(long owner, long through);
    private static native void release0(long owner, long id);
    private static native String status0(long owner);
    private static native void retire(long owner);
}
