package com.google.ai.edge.examples.asr;

import java.util.Arrays;
import org.apache.commons.math3.complex.Complex;
import org.apache.commons.math3.transform.DftNormalization;
import org.apache.commons.math3.transform.FastFourierTransformer;
import org.apache.commons.math3.transform.TransformType;

/**
 * Forward-only, sparse implementation of the pinned JLibrosa reference math.
 * Reference: Subtitle-Synchronizer/jlibrosa dcf67da, AudioFeatureExtraction.
 * Keeps the FFT library, reflection, Hann window, Slaney coefficients and
 * per-addition float rounding. No unused inverse transforms or reconstructions.
 * Not a general replacement: this model's exact 16k/512/128/160 contract only.
 */
public final class SparseMelSpectrogram {
    private static final int FFT = 512, MELS = 128, HOP = 160, BINS = 257;
    private static final double[] WINDOW = window();
    private static final Filter[] FILTERS = filters();

    private SparseMelSpectrogram() {}
    private static final class Filter {
        final int[] bins;
        final double[] weights;
        Filter(int[] bins, double[] weights) { this.bins = bins; this.weights = weights; }
    }

    public static float[][] generate(float[] audio, int rate, int fft, int mels, int hop) {
        if (rate != 16000 || fft != FFT || mels != MELS || hop != HOP) {
            throw new IllegalArgumentException("Unqualified mel configuration");
        }
        if (audio.length < 258 || audio.length > 80000) {
            throw new IllegalArgumentException("Expected uncut 258..80000 sample window");
        }
        double[] padded = new double[audio.length + FFT];
        for (int i = 0; i < audio.length; i++) {
            if (!Float.isFinite(audio[i])) throw new IllegalArgumentException("Nonfinite PCM");
            padded[FFT / 2 + i] = audio[i];
        }
        for (int i = 0; i < FFT / 2; i++) {
            padded[FFT / 2 - i - 1] = audio[i + 1];
            padded[FFT / 2 + audio.length + i] = audio[audio.length - 2 - i];
        }
        int frames = 1 + audio.length / HOP;
        float[][] result = new float[MELS][frames];
        double[] frame = new double[FFT];
        double[] power = new double[BINS];
        FastFourierTransformer transform = new FastFourierTransformer(DftNormalization.STANDARD);
        for (int t = 0; t < frames; t++) {
            for (int k = 0; k < FFT; k++) frame[k] = WINDOW[k] * padded[t * HOP + k];
            Complex[] spectrum = transform.transform(frame, TransformType.FORWARD);
            for (int k = 0; k < BINS; k++) {
                double magnitude = Math.sqrt(Math.pow(spectrum[k].getReal(), 2)
                    + Math.pow(spectrum[k].getImaginary(), 2));
                power[k] = Math.pow(magnitude, 2);
                if (!Double.isFinite(power[k])) throw new IllegalArgumentException("Nonfinite spectrum");
            }
            for (int m = 0; m < MELS; m++) {
                Filter filter = FILTERS[m];
                float sum = 0;
                // Increasing bin order, exactly one float rounding per nonzero term.
                for (int k = 0; k < filter.bins.length; k++) sum += filter.weights[k] * power[filter.bins[k]];
                result[m][t] = sum;
            }
        }
        return result;
    }

    private static double[] window() {
        double[] values = new double[FFT];
        for (int i = 0; i < FFT; i++) values[i] = 0.5 - 0.5 * Math.cos(2.0 * Math.PI * i / FFT);
        return values;
    }

    private static Filter[] filters() {
        double step = 200.0 / 3;
        double threshold = 1000.0 / step;
        double logStep = Math.log(6.4) / 27.0;
        double maxMel = threshold + Math.log(8000.0 / 1000.0) / logStep;
        double[] frequencies = new double[MELS + 2];
        for (int i = 0; i < frequencies.length; i++) {
            double mel = 0.0 + (maxMel - 0.0) / (frequencies.length - 1) * i;
            frequencies[i] = mel < threshold ? 0.0 + step * mel : 1000.0 * Math.exp(logStep * (mel - threshold));
        }
        Filter[] result = new Filter[MELS];
        for (int m = 0; m < MELS; m++) {
            int[] bins = new int[BINS];
            double[] weights = new double[BINS];
            int count = 0;
            double norm = 2.0 / (frequencies[m + 2] - frequencies[m]);
            for (int k = 0; k < BINS; k++) {
                double hz = 0.0 + (16000.0 / 2) / (FFT / 2) * k;
                double lower = -(frequencies[m] - hz) / (frequencies[m + 1] - frequencies[m]);
                double upper = (frequencies[m + 2] - hz) / (frequencies[m + 2] - frequencies[m + 1]);
                double value = 0.0;
                // Match the reference's strict branches, including its equality case.
                if (lower > upper && upper > 0) value = upper;
                else if (lower < upper && lower > 0) value = lower;
                value *= norm;
                if (value != 0.0) { bins[count] = k; weights[count] = value; count++; }
            }
            result[m] = new Filter(Arrays.copyOf(bins, count), Arrays.copyOf(weights, count));
        }
        return result;
    }
}
