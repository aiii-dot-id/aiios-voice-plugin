package com.google.ai.edge.examples.asr;

// Private recorded-input integration probe. Production embedding uses the
// existing native Session/C ABI; this method is not a Plugin SDK control.
public final class NativeVoiceSession {
    static { System.loadLibrary("voice_prefix"); }
    public static native int run(NativePrefixRecognizer.Decoder decoder,
                                 float[] mel, String[] arguments, String logRoot);
}
