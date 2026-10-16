package com.google.ai.edge.examples.asr

import android.app.Activity
import android.content.pm.ApplicationInfo
import android.os.Build
import android.os.Bundle
import android.os.Debug
import android.util.Log
import com.google.ai.edge.litert.Accelerator
import com.google.ai.edge.litert.CompiledModel
import com.google.ai.edge.litert.Environment
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.sqrt

// Small exact-activation boundary diagnostic. No microphone, CPU fallback,
// speech recognition claim, or change to any installed production identity.
class MainActivity : Activity() {
    private fun sha(raw: ByteArray) = MessageDigest.getInstance("SHA-256").digest(raw)
        .joinToString("") { "%02x".format(it) }
    private fun read(row: JSONObject): ByteArray {
        val raw = assets.open(row.getString("file")).readBytes()
        check(raw.size == row.getInt("bytes") && sha(raw) == row.getString("sha256"))
        return raw
    }
    private fun floats(row: JSONObject): FloatArray {
        val buffer = ByteBuffer.wrap(read(row)).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer()
        return FloatArray(buffer.remaining()) { buffer.get() }.also { check(it.all { x -> x.isFinite() }) }
    }
    private fun errors(actual: FloatArray, expected: FloatArray): JSONObject {
        check(actual.size == expected.size)
        var bad = 0; var nonfinite = 0; var worst = 0.0; var squared = 0.0
        for (i in actual.indices) {
            if (!actual[i].isFinite()) { nonfinite++; bad++; continue }
            val difference = abs(actual[i].toDouble() - expected[i].toDouble())
            worst = max(worst, difference); squared += difference * difference
            if (difference > 0.0005 + 0.001 * abs(expected[i].toDouble())) bad++
        }
        return JSONObject().put("values", actual.size).put("outside_tolerance", bad).put("nonfinite", nonfinite)
            .put("finite_max_abs", worst).put("finite_rms_over_all", sqrt(squared / actual.size))
    }
    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        val root = checkNotNull(getExternalFilesDir(null))
        val id = checkNotNull(intent.getStringExtra("run_id")); check(id.matches(Regex("[a-f0-9]{32}")))
        val directory = File(root, "run-$id"); check(directory.mkdir())
        Thread {
            val trials = JSONArray(); val captures = JSONArray()
            val result = JSONObject().put("diagnostic_completed", false).put("parity_passed", false)
                .put("voice_platform_qualified", false).put("cpu_fallback_allowed", false)
                .put("run_id", id).put("pid", android.os.Process.myPid())
                .put("model", Build.MODEL).put("fingerprint", Build.FINGERPRINT).put("trials", trials).put("captured", captures)
                .put("debuggable", applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0)
            try {
                check(Build.MODEL == "Pixel 10 Pro" && !result.getBoolean("debuggable"))
                val fixtureBytes = assets.open("normalization-fixture.json").readBytes()
                val fixture = JSONObject(String(fixtureBytes)); result.put("fixture_sha256", sha(fixtureBytes))
                val backend = fixture.optString("accelerator", "NPU")
                check(backend == "NPU" || backend == "GPU")
                val accelerator = if (backend == "NPU") Accelerator.NPU else Accelerator.GPU
                result.put("accelerator_requested", backend)
                Environment.create(mapOf(Environment.Option.DispatchLibraryDir to applicationInfo.nativeLibraryDir)).use { env ->
                    check(env.getAvailableAccelerators().contains(accelerator)) { "$backend unavailable; no fallback" }
                    val models = fixture.getJSONArray("models"); var allPassed = true
                    for (m in 0 until models.length()) {
                        val bound = models.getJSONObject(m); val precision = bound.getString("precision")
                        val file = File(directory, "$precision.tflite"); file.writeBytes(read(bound))
                        val options = CompiledModel.Options(accelerator)
                        if (backend == "GPU") {
                            check(precision == "fp32")
                            options.gpuOptions = CompiledModel.GpuOptions(precision = CompiledModel.GpuOptions.Precision.FP32)
                        }
                        CompiledModel.create(file.absolutePath, options, env).use { model ->
                            val inputs = model.createInputBuffers("serving_default")
                            try {
                                val outputs = model.createOutputBuffers("serving_default")
                                try {
                                    check(inputs.size == 1 && outputs.size == 2)
                                    val cases = fixture.getJSONArray("cases")
                                    repeat(2) { repeat -> for (c in 0 until cases.length()) {
                                        val item = cases.getJSONObject(c); val expected = floats(item.getJSONObject("reference"))
                                        val input = floats(item.getJSONObject("input")); check(input.size == 250 * 1024)
                                        inputs[0].writeFloat(input); val began = System.nanoTime()
                                        model.run(inputs, outputs, "serving_default")
                                        val row = JSONObject().put("precision", precision).put("case", c).put("name", item.getString("name"))
                                            .put("repeat", repeat).put("ms", (System.nanoTime() - began) / 1e6).put("pss_kib", Debug.getPss())
                                        for (o in 0..1) {
                                            val values = outputs[o].readFloat(); check(values.size == input.size)
                                            val stats = errors(values, expected); row.put(if (o == 0) "original" else "scaled", stats)
                                            allPassed = allPassed && stats.getInt("outside_tolerance") == 0
                                            val raw = ByteBuffer.allocate(values.size * 4).order(ByteOrder.LITTLE_ENDIAN)
                                            raw.asFloatBuffer().put(values)
                                            val name = "$precision-case-$c-repeat-$repeat-output-$o.f32"
                                            File(directory, name).writeBytes(raw.array())
                                            captures.put(JSONObject().put("file", name).put("bytes", raw.capacity()).put("sha256", sha(raw.array())))
                                        }
                                        trials.put(row); Log.i("AII-PixelNorm", row.toString())
                                    } }
                                } finally { outputs.forEach { it.close() } }
                            } finally { inputs.forEach { it.close() } }
                        }
                    }
                    result.put("parity_passed", allPassed).put("diagnostic_completed", true)
                }
            } catch (error: Throwable) {
                result.put("error", "${error.javaClass.name}: ${error.message}"); Log.e("AII-PixelNorm", "Diagnostic failed", error)
            } finally {
                val temp = File(directory, "result.json.tmp"); temp.writeText(result.toString(2))
                check(temp.renameTo(File(directory, "result.json"))); runOnUiThread { finish() }
            }
        }.start()
    }
}
