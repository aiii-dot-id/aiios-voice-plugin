package com.google.ai.edge.examples.asr;

public final class NativeVoiceSession {
    static { System.loadLibrary("voice_prefix"); }
    public static native int run(NativePrefixRecognizer.Decoder decoder,
                                 float[] mel, String[] arguments, String logRoot);
}
