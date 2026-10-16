package id.aiii.voice.tensorsdkcheck

import android.app.Activity
import android.os.Bundle
import android.os.Build
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

/** SDK/device compatibility only; this model is NOT our speech engine. */
class MainActivity : Activity() {
    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        Thread {
            val result = JSONObject().put("passed", false)
                .put("accelerator_requested", "NPU").put("cpu_fallback_allowed", false)
                .put("voice_qualified", false).put("fingerprint", Build.FINGERPRINT)
                .put("model", Build.MODEL).put("runtime_version", "2.2.0")
            try {
                val path = File(filesDir, "segmentation.tflite")
                assets.open("segmentation.tflite").use { src -> path.outputStream().use { src.copyTo(it) } }
                val digest = sha(path.readBytes())
                check(digest == "a8e8b889af0c15c0cbb4f89762b9304cfc1678928f6593abed9903b0b39f0c3e")
                result.put("model_sha256", digest)
                val started = System.nanoTime()
                Environment.create(mapOf(Environment.Option.DispatchLibraryDir to applicationInfo.nativeLibraryDir)).use { env ->
                    val available = env.javaClass.getMethod("getAvailableAccelerators").invoke(env).toString()
                    result.put("available", available)
                    check(available.contains("NPU")) { "NPU unavailable; refusing CPU substitution" }
                    CompiledModel.create(path.absolutePath, CompiledModel.Options(Accelerator.NPU), env).use { model ->
                        result.put("create_ms", (System.nanoTime() - started) / 1e6)
                        val inputs = model.createInputBuffers()
                        val outputs = model.createOutputBuffers()
                        try {
                            val trials = JSONArray()
                            var previous: String? = null
                            repeat(8) { index ->
                                inputs[0].writeFloat(FloatArray(256 * 256 * 3) { if (index < 7) 0f else (it % 251) / 251f })
                                val start = System.nanoTime()
                                model.run(inputs, outputs)
                                val ms = (System.nanoTime() - start) / 1e6
                                val values = outputs[0].readFloat()
                                check(values.size == 256 * 256 * 6 && values.all { it.isFinite() })
                                val bytes = ByteBuffer.allocate(values.size * 4).order(ByteOrder.LITTLE_ENDIAN)
                                values.forEach { bytes.putFloat(it) }
                                val outputHash = sha(bytes.array())
                                if (index in 1..6) check(outputHash == previous) { "NPU output changed for identical input" }
                                if (index == 7) check(outputHash != previous) { "Output did not respond to changed input" }
                                previous = outputHash
                                trials.put(JSONObject().put("index", index).put("run_ms", ms)
                                    .put("output_sha256", outputHash).put("output_floats", values.size))
                            }
                            result.put("trials", trials).put("passed", true)
                        } finally { inputs.forEach { it.close() }; outputs.forEach { it.close() } }
                    }
                }
            } catch (t: Throwable) {
                result.put("error", "${t.javaClass.name}: ${t.message}")
                Log.e("AII-TensorSDK", "NPU check failed", t)
            } finally {
                File(filesDir, "result.json").writeText(result.toString(2))
                Log.i("AII-TensorSDK", result.toString())
                runOnUiThread { finish() }
            }
        }.start()
    }
    private fun sha(bytes: ByteArray) = MessageDigest.getInstance("SHA-256")
        .digest(bytes).joinToString("") { "%02x".format(it) }
}
