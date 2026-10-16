package com.google.ai.edge.examples.asr

import android.app.Activity
import android.os.Build
import android.os.Bundle
import android.os.Debug
import android.util.Log
import com.google.ai.edge.litert.Accelerator
import com.google.ai.edge.litert.CompiledModel
import com.google.ai.edge.litert.Environment
import com.google.ai.edge.litert.TensorBuffer
import com.google.ai.edge.litert.TensorType
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest

// Compatibility types for the two byte-bound Google reference components.
// LiteRT 2.2.0 exposes layout.dimensions, not the sample's numElements accessor.
val TensorType.numElements: Int
    get() = checkNotNull(layout) { "Tensor layout unavailable" }.dimensions.fold(1) { product, dimension ->
        check(dimension > 0) { "Unresolved tensor dimension" }
        Math.multiplyExact(product, dimension)
    }
data class LogMelSpectroConfig(
    val nFFT: Int = 512, val nMels: Int = 128, val hopLength: Int = 160,
    val nFrames: Int = 500, val transpose: Boolean = false,
    val preemphasis: Float = 0.97f, val normType: String = "standard")
interface AudioPreprocessor : AutoCloseable { fun process(rawSpeech: FloatArray): FloatArray }
data class ModelConfig(val decodeStartTokenId: Int = 8192)
object SpeechRecognizer { const val END_OF_SEQUENCE = -2 }
class LiteRtRunner {
    interface Decoder { fun decode(encodeOutputBuffers: List<TensorBuffer>): Sequence<Pair<Int, Int>> }
    companion object {
        const val DECODE_SIGNATURE = "decode"
        fun inputBufferName(index: Int) = "args_$index"
        fun outputBufferName(index: Int) = "output_$index"
    }
}

/** Recorded speech control only: no microphone, identity, or voice enrollment. */
class MainActivity : Activity() {
    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        val modelDir = getExternalFilesDir(null) ?: error("Own model directory unavailable")
        if (intent.getBooleanExtra("prepare_only", false)) { finish(); return }
        val requestId = intent.getStringExtra("run_id") ?: error("Run identity required")
        val resultFile = File(filesDir, "result.json")
        check(!resultFile.exists() || resultFile.delete()) { "Cannot retire prior test result" }
        Thread {
            val report = JSONObject().put("passed", false).put("voice_platform_qualified", false)
                .put("run_id", requestId)
                .put("cpu_fallback_allowed", false).put("accelerator_requested", "NPU")
                .put("model", Build.MODEL).put("fingerprint", Build.FINGERPRINT)
                .put("runtime", "LiteRT 2.2.0").put("pid", android.os.Process.myPid())
            try {
                check(Build.MODEL == "Pixel 10 Pro") { "Wrong physical target" }
                val manifest = JSONObject(assets.open("fixture.json").bufferedReader().readText())
                val modelFile = File(modelDir, manifest.getString("model_file"))
                check(modelFile.length() == manifest.getLong("model_bytes")) { "Model size mismatch" }
                check(sha(modelFile) == manifest.getString("model_sha256")) { "Model hash mismatch" }
                report.put("model_sha256", manifest.getString("model_sha256"))
                val began = System.nanoTime()
                Environment.create(mapOf(Environment.Option.DispatchLibraryDir to applicationInfo.nativeLibraryDir)).use { env ->
                    val available = env.javaClass.getMethod("getAvailableAccelerators").invoke(env).toString()
                    report.put("available_accelerators", available)
                    check(available.contains("NPU")) { "NPU unavailable; refusing CPU substitution" }
                    CompiledModel.create(modelFile.absolutePath, CompiledModel.Options(Accelerator.NPU), env).use { model ->
                        report.put("load_ms", (System.nanoTime() - began) / 1e6).put("load_pss_kib", Debug.getPss())
                        val inputs = model.createInputBuffers("encode")
                        val outputs = model.createOutputBuffers("encode")
                        val decoder = TdtDecoder(model, ModelConfig())
                        try {
                            check(inputs.size == 1)
                            check(model.getInputTensorType("args_0", "encode").numElements == 128 * 500)
                            val trials = JSONArray()
                            val cases = manifest.getJSONArray("cases")
                            repeat(2) { repeatIndex ->
                                for (index in 0 until cases.length()) {
                                    val item = cases.getJSONObject(index)
                                    val raw = assets.open(item.getString("file")).readBytes()
                                    check(sha(raw) == item.getString("pcm_sha256")) { "Input PCM mismatch" }
                                    check(raw.size % 2 == 0 && raw.size in 2..160000) { "No input clipping permitted" }
                                    val shorts = ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer()
                                    val audio = FloatArray(shorts.remaining()) { shorts.get().toFloat() / 32768f }
                                    val frontBegin = System.nanoTime()
                                    val features = MelSpectroProcessor(16000, LogMelSpectroConfig()).process(audio)
                                    check(features.size == 128 * 500 && features.all { it.isFinite() })
                                    val frontMs = (System.nanoTime() - frontBegin) / 1e6
                                    inputs[0].writeFloat(features)
                                    val runBegin = System.nanoTime()
                                    model.run(inputs, outputs, "encode")
                                    val encodeMs = (System.nanoTime() - runBegin) / 1e6
                                    val decodeBegin = System.nanoTime()
                                    val tokens = JSONArray()
                                    var endings = 0
                                    decoder.decode(outputs).forEach { (token, frame) ->
                                        if (token == SpeechRecognizer.END_OF_SEQUENCE) endings++
                                        else { check(token in 0..8191); tokens.put(JSONArray().put(token).put(frame)) }
                                    }
                                    check(endings == 1) { "No complete decode" }
                                    val decodeMs = (System.nanoTime() - decodeBegin) / 1e6
                                    val row = JSONObject().put("id", item.getString("id")).put("repeat", repeatIndex)
                                        .put("pcm_sha256", item.getString("pcm_sha256")).put("samples", audio.size)
                                        .put("frontend_ms", frontMs).put("encode_ms", encodeMs).put("decode_ms", decodeMs)
                                        .put("tokens", tokens).put("pss_kib", Debug.getPss()).put("complete", true)
                                    trials.put(row)
                                    Log.i("AII-PixelASR", row.toString())
                                }
                            }
                            report.put("trials", trials).put("passed", true)
                        } finally {
                            decoder.closeReferenceBuffers()
                            inputs.forEach { it.close() }; outputs.forEach { it.close() }
                        }
                    }
                }
            } catch (t: Throwable) {
                report.put("error", "${t.javaClass.name}: ${t.message}")
                Log.e("AII-PixelASR", "Speech reference failed", t)
            } finally {
                val temporary = File(filesDir, "result.json.tmp")
                temporary.writeText(report.toString(2))
                check(temporary.renameTo(resultFile)) { "Cannot publish complete test result" }
                Log.i("AII-PixelASR", report.toString())
                runOnUiThread { finish() }
            }
        }.start()
    }
    private fun sha(raw: ByteArray) = MessageDigest.getInstance("SHA-256").digest(raw)
        .joinToString("") { "%02x".format(it) }
    private fun sha(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(1024 * 1024)
            while (true) { val n = input.read(buffer); if (n < 0) break; digest.update(buffer, 0, n) }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}
