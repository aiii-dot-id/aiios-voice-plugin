package com.google.ai.edge.examples.asr;

import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicLong;

/** Private native model adapter. GPU resources are created, used and closed
 * on the C++ recognizer's owned inference thread. No private LiteRT handles.
 * cancel() is admission only; reset()/close() wait for actual retirement. */
public final class NativePrefixRecognizer implements AutoCloseable {
    static { System.loadLibrary("voice_prefix"); }
    public interface Decoder {
        void open();
        byte[] decode(float[] features, int validFrames, long cancellation);
        void close();
    }
    private final AtomicLong owner;
    public NativePrefixRecognizer(Decoder decoder, float[] mel) {
        owner = new AtomicLong(create(decoder, mel));
    }
    private long handle() {
        long h=owner.get();
        if(h==0)throw new IllegalStateException("native prefix owner retired");
        return h;
    }
    public void begin() { begin0(handle()); }
    public String push(float[] pcm,int offset,int count) {
        return new String(push0(handle(),pcm,offset,count),StandardCharsets.UTF_8);
    }
    public String finish() { return new String(finish0(handle()),StandardCharsets.UTF_8); }
    public void reset() { reset0(handle()); }
    public void cancel() { cancel0(handle()); }
    public static native void checkpoint(long cancellation);
    @Override public void close() { long h=owner.getAndSet(0);if(h!=0)retire(h); }
    private static native long create(Decoder decoder,float[] mel);
    private static native void begin0(long h);
    private static native byte[] push0(long h,float[] pcm,int offset,int count);
    private static native byte[] finish0(long h);
    private static native void reset0(long h);
    private static native void cancel0(long h);
    private static native void retire(long h);
}
