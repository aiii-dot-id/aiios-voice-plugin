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
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

// Physical conversion gate, not a production session or microphone owner.
// All model operations request the declared accelerator exclusively, never CPU.
// Reference vectors never replace
// the closed-loop decoder's own encoder output or recurrent state.
class MainActivity : Activity() {
    private lateinit var outputDir: File
    private var captured = JSONArray()
    private var activeTrial: JSONObject? = null
    private var pcmOwner: NativeLengthPcm? = null
    private var generation = 0L
    private fun checkpoint() { pcmOwner?.check(generation) }
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
    private fun bytes(fields: JSONObject, key: String): ByteArray {
        val row = fields.getJSONObject(key)
        val raw = assets.open(row.getString("file")).readBytes()
        check(raw.size == row.getInt("bytes") && sha(raw) == row.getString("sha256")) { "Fixture mismatch: $key" }
        return raw
    }
    private fun floats(fields: JSONObject, key: String): FloatArray {
        val b = ByteBuffer.wrap(bytes(fields, key)).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer()
        return FloatArray(b.remaining()) { b.get() }.also { check(it.all { x -> x.isFinite() }) }
    }
    private fun longs(fields: JSONObject, key: String): LongArray {
        val b = ByteBuffer.wrap(bytes(fields, key)).order(ByteOrder.LITTLE_ENDIAN).asLongBuffer()
        return LongArray(b.remaining()) { b.get() }
    }
    private fun capture(name: String, data: FloatArray) {
        val raw = ByteBuffer.allocate(data.size * 4).order(ByteOrder.LITTLE_ENDIAN)
        raw.asFloatBuffer().put(data)
        val file = File(outputDir, name); check(!file.exists()); file.writeBytes(raw.array())
        captured.put(JSONObject().put("file", name).put("bytes", raw.capacity()).put("sha256", sha(raw.array())))
    }
    private fun publish(name: String, value: JSONObject) {
        val temp = File(outputDir, "$name.tmp"); temp.writeText(value.toString(2))
        check(temp.renameTo(File(outputDir, name)))
    }
    private class ErrorStats {
        var count = 0L; var failures = 0L; var worst = 0.0; var squares = 0.0
        fun add(actual: FloatArray, expected: FloatArray) {
            check(actual.size == expected.size) { "Output extent changed" }
            for (i in actual.indices) {
                check(actual[i].isFinite() && expected[i].isFinite()) { "Nonfinite output/reference" }
                val difference = abs(actual[i].toDouble() - expected[i].toDouble())
                worst = max(worst, difference); squares += difference * difference; count++
                if (difference > 0.0005 + 0.001 * abs(expected[i].toDouble())) failures++
            }
        }
        fun json() = JSONObject().put("values", count).put("outside_tolerance", failures)
            .put("max_abs", worst).put("rms", if (count == 0L) 0.0 else sqrt(squares / count))
        fun passed() = count > 0 && failures == 0L
    }
    private class Graph(val model: CompiledModel, val integerInput32: Boolean = false,
                        val checkpoint: () -> Unit = {}) : AutoCloseable {
        var afterRun: (() -> Unit)? = null // Private held-after-real-call falsifier only.
        val input = try { model.createInputBuffers("serving_default") }
            catch (error: Throwable) { model.close(); throw error }
        val output = try { model.createOutputBuffers("serving_default") }
            catch (error: Throwable) { input.forEach { it.close() }; model.close(); throw error }
        fun run() { checkpoint(); model.run(input, output, "serving_default"); afterRun?.invoke(); checkpoint() }
        fun writeInteger(index: Int, values: LongArray, maximum: Long) {
            check(values.all { it in 0..maximum }) { "Integer input outside admitted domain" }
            if (integerInput32) input[index].writeInt(IntArray(values.size) { values[it].toInt() })
            else input[index].writeLong(values)
        }
        override fun close() { input.forEach { it.close() }; output.forEach { it.close() }; model.close() }
    }
    private fun argmax(values: FloatArray, start: Int, end: Int): Int {
        check(start >= 0 && start < end && end <= values.size)
        var best = start
        for (i in start until end) {
            check(values[i].isFinite()) { "Nonfinite logits cannot produce a token" }
            if (values[i] > values[best]) best = i
        }
        return best - start
    }
    private fun frontendEdges(edges: JSONArray): JSONArray {
        val results=JSONArray();val owner=checkNotNull(pcmOwner)
        for (i in 0 until edges.length()) {
            val item=edges.getJSONObject(i);val fields=item.getJSONObject("fields")
            val pcm=floats(fields,"pcm");val expected=floats(fields,"reference")
            check(pcm.size==item.getInt("samples") && expected.size==256000)
            generation++;owner.begin(generation)
            val actual=owner.process(generation,pcm);checkpoint()
            var failed=0;var worst=0.0
            check(actual.size==expected.size)
            for (k in actual.indices) {
                check(actual[k].isFinite());val diff=abs(actual[k].toDouble()-expected[k].toDouble())
                worst=max(worst,diff)
                if(diff>0.0001+0.0001*abs(expected[k].toDouble()))failed++
            }
            results.put(JSONObject().put("kind",item.getString("kind")).put("samples",pcm.size)
                .put("outside_tolerance",failed).put("max_abs",worst).put("generation",generation))
            check(failed==0) { "Native PCM boundary failed: ${results.getJSONObject(i)}" }
        }
        return results
    }
    private fun slice(values: FloatArray, row: Int, stride: Int) = values.copyOfRange(row * stride, (row + 1) * stride)
    private fun trial(index: Int, repeat: Int, item: JSONObject, enc: Graph, pred: Graph, joint: Graph,
                      captureTrace: Boolean = true): JSONObject {
        val fields = item.getJSONObject("fields")
        val row = JSONObject().put("case", index).put("repeat", repeat).put("reference_text", item.getString("reference_text"))
        activeTrial = row
        // Reference data is diagnostic only. Actual features come from native
        // PCM when configured; teacher features never replace that result.
        val referenceFeatures = floats(fields, "features")
        val pcm = if (pcmOwner != null) floats(fields, "pcm") else null
        generation++; pcmOwner?.begin(generation)
        val completeStart = System.nanoTime()
        val frontendStart = System.nanoTime()
        val features = if (pcm != null) checkNotNull(pcmOwner).process(generation, pcm) else referenceFeatures
        checkpoint()
        row.put("frontend_ms", (System.nanoTime() - frontendStart) / 1e6)
            .put("input_kind", if (pcm != null) "native_pcm" else "frozen_features")
            .put("input_samples", pcm?.size ?: 0).put("generation", generation)
        enc.input[0].writeFloat(features)
        enc.writeInteger(1, if (pcm != null) longArrayOf((pcm.size / 160).toLong()) else longs(fields, "valid_frames"), 2000)
        val start = System.nanoTime(); enc.run()
        row.put("encode_ms", (System.nanoTime() - start) / 1e6)
        val encoded = enc.output[0].readFloat(); val length = enc.output[1].readInt()
        checkpoint()
        row.put("encoder_output_ready_ms", (System.nanoTime() - start) / 1e6)
        check(encoded.size == 250 * 640 && length.size == 1 && length[0] in 1..250)
        row.put("valid_encoder_frames", length[0]).put("expected_encoder_frames", item.getInt("encoder_length"))
        if (captureTrace) capture("case-$index-repeat-$repeat-encoder.f32", encoded)
        check(length[0] == item.getInt("encoder_length")) { "Encoder length changed: observed=${length[0]} expected=${item.getInt("encoder_length")}" }
        val encoderError = ErrorStats(); encoderError.add(encoded, floats(fields, "encoder_output"))
        row.put("encoder_error", encoderError.json())
        val paddingZeros = encoded.sliceArray(length[0] * 640 until encoded.size).all { it == 0f }
        row.put("canonical_padding", paddingZeros)

        val pToken = longs(fields, "p_token"); val pHidden = floats(fields, "p_hidden"); val pCell = floats(fields, "p_cell")
        val pOut = floats(fields, "p_output"); val pNextHidden = floats(fields, "p_next_hidden"); val pNextCell = floats(fields, "p_next_cell")
        val jFrame = floats(fields, "j_frame"); val jPrediction = floats(fields, "j_prediction"); val jLogits = floats(fields, "j_logits")
        val teacherPrediction = ErrorStats(); val teacherState = ErrorStats(); val teacherJoint = ErrorStats()
        var teacherDecisions = true
        // Isolated component checks use fixed teacher inputs only in this block.
        for (i in pToken.indices) {
            pred.writeInteger(0, longArrayOf(pToken[i]), 8192); pred.input[1].writeFloat(slice(pHidden, i, 1280)); pred.input[2].writeFloat(slice(pCell, i, 1280)); pred.run()
            teacherPrediction.add(pred.output[0].readFloat(), slice(pOut, i, 640))
            teacherState.add(pred.output[1].readFloat(), slice(pNextHidden, i, 1280))
            teacherState.add(pred.output[2].readFloat(), slice(pNextCell, i, 1280))
        }
        for (i in 0 until jLogits.size / 8198) {
            joint.input[0].writeFloat(slice(jFrame, i, 640)); joint.input[1].writeFloat(slice(jPrediction, i, 640)); joint.run()
            val actual = joint.output[0].readFloat(); val wanted = slice(jLogits, i, 8198)
            teacherJoint.add(actual, wanted)
            teacherDecisions = teacherDecisions && argmax(actual, 0, 8193) == argmax(wanted, 0, 8193) && argmax(actual, 8193, 8198) == argmax(wanted, 8193, 8198)
        }
        row.put("teacher_prediction_error", teacherPrediction.json()).put("teacher_state_error", teacherState.json())
            .put("teacher_joint_error", teacherJoint.json()).put("teacher_joint_decisions_exact", teacherDecisions)

        // Fresh zero state, then only the device's own outputs. Never insert a
        // reference token/state to get back onto the expected trajectory.
        var hidden = FloatArray(1280); var cell = FloatArray(1280); var pIndex = 0
        val stateError = ErrorStats(); val predictionError = ErrorStats(); val jointError = ErrorStats()
        fun predict(token: Long, compareReference: Boolean): FloatArray {
            pred.writeInteger(0, longArrayOf(token), 8192); pred.input[1].writeFloat(hidden); pred.input[2].writeFloat(cell); pred.run()
            val prediction = pred.output[0].readFloat(); hidden = pred.output[1].readFloat(); cell = pred.output[2].readFloat()
            check(prediction.all { it.isFinite() } && hidden.all { it.isFinite() } && cell.all { it.isFinite() })
            if (compareReference && pIndex < pToken.size) {
                predictionError.add(prediction, slice(pOut, pIndex, 640)); stateError.add(hidden, slice(pNextHidden, pIndex, 1280)); stateError.add(cell, slice(pNextCell, pIndex, 1280))
            }
            pIndex++; return prediction
        }
        val loopStart = System.nanoTime(); var prediction = predict(8192, true)
        var frame = 0; val steps = JSONArray(); val tokens = JSONArray(); val expected = item.getJSONArray("expected_steps")
        var aligned = true; val capturedLogits = ArrayList<FloatArray>()
        while (frame < length[0]) {
            check(steps.length() < 2048) { "Decode budget exhausted; no complete transcript" }
            joint.input[0].writeFloat(slice(encoded, frame, 640)); joint.input[1].writeFloat(prediction); joint.run()
            val logits = joint.output[0].readFloat(); check(logits.size == 8198)
            capturedLogits.add(logits)
            if (aligned && steps.length() < expected.length()) jointError.add(logits, slice(jLogits, steps.length(), 8198))
            val token = argmax(logits, 0, 8193); val duration = argmax(logits, 8193, 8198)
            val step = JSONArray().put(frame).put(token).put(duration)
            if (steps.length() >= expected.length() || step.toString() != expected.getJSONArray(steps.length()).toString()) aligned = false
            steps.put(step)
            if (token != 8192) { tokens.put(token); prediction = predict(token.toLong(), aligned) }
            frame += if (token == 8192 && duration == 0) 1 else duration
        }
        row.put("decode_ms", (System.nanoTime() - loopStart) / 1e6)
        checkpoint()
        // Includes the diagnostic comparisons and teacher checks above. This
        // is a measured PCM-to-complete harness interval, NOT production latency.
        row.put("input_to_complete_with_diagnostics_ms", (System.nanoTime() - completeStart) / 1e6)
        val allLogits = FloatArray(capturedLogits.size * 8198)
        capturedLogits.forEachIndexed { i, values -> values.copyInto(allLogits, i * 8198) }
        if (captureTrace) capture("case-$index-repeat-$repeat-logits.f32", allLogits)
        var featureFailures = 0; var featureWorst = 0.0
        for (i in features.indices) {
            check(features[i].isFinite())
            val difference = abs(features[i].toDouble() - referenceFeatures[i].toDouble())
            featureWorst = max(featureWorst, difference)
            if (difference > 0.0001 + 0.0001 * abs(referenceFeatures[i].toDouble())) featureFailures++
        }
        row.put("feature_outside_tolerance", featureFailures).put("feature_max_abs", featureWorst)
        val exact = steps.toString() == expected.toString() && tokens.toString() == item.getJSONArray("expected_tokens").toString() && pIndex == pToken.size
        row.put("steps", steps).put("tokens", tokens).put("advanced_frames", frame).put("predictor_calls", pIndex)
            .put("decisions_exact", exact).put("loop_prediction_error", predictionError.json()).put("loop_state_error", stateError.json())
            .put("loop_joint_error_until_divergence", jointError.json()).put("complete", true).put("pss_kib", Debug.getPss())
        row.put("passed", featureFailures == 0 && exact && paddingZeros && encoderError.passed() && teacherPrediction.passed() && teacherState.passed() && teacherJoint.passed() && teacherDecisions && predictionError.passed() && stateError.passed() && jointError.passed())
        checkpoint()
        activeTrial = null; return row
    }
    private fun controlProbe(item: JSONObject, enc: Graph, pred: Graph, joint: Graph, phase: String, closing: Boolean): JSONObject {
        val owner = checkNotNull(pcmOwner); generation++; val id = generation
        owner.begin(id)
        val pcm = floats(item.getJSONObject("fields"), "pcm")
        val held = CountDownLatch(1); val release = CountDownLatch(1)
        val returned = CountDownLatch(1); val control = AtomicReference<JSONObject>()
        val controlFault = AtomicReference<Throwable>()
        val target = if (phase == "encoder") enc else joint
        target.afterRun = { held.countDown(); check(release.await(5, TimeUnit.SECONDS)) { "Diagnostic release timed out" } }
        val driver = Thread {
            try {
                check(held.await(15, TimeUnit.SECONDS)) { "No completed GPU call to hold" }
                val start = System.nanoTime()
                if (closing) owner.close() else owner.cancel(id)
                val elapsed = (System.nanoTime() - start) / 1e6
                // Cancellation must return while the inference thread remains
                // deliberately held, not release or await inference itself.
                check(returned.count == 1L && release.count == 1L)
                control.set(JSONObject().put("control_ms", elapsed).put("returned_while_inference_held", true))
            } catch (error: Throwable) { controlFault.set(error) }
            finally { release.countDown() }
        }
        driver.start(); var refusal: String? = null; var published = false
        try {
            val features = owner.process(id, pcm); checkpoint()
            enc.input[0].writeFloat(features); enc.writeInteger(1, longArrayOf((pcm.size / 160).toLong()), 2000); enc.run()
            val encoded = enc.output[0].readFloat(); checkpoint()
            if (phase == "joint") {
                pred.writeInteger(0, longArrayOf(8192), 8192)
                pred.input[1].writeFloat(FloatArray(1280)); pred.input[2].writeFloat(FloatArray(1280)); pred.run()
                joint.input[0].writeFloat(slice(encoded, 0, 640)); joint.input[1].writeFloat(pred.output[0].readFloat()); joint.run()
            }
            checkpoint(); published = true
        } catch (error: IllegalStateException) { refusal = error.message }
        finally { returned.countDown(); release.countDown(); driver.join(16000); target.afterRun = null }
        check(!driver.isAlive && controlFault.get() == null) { "Control driver failed: ${controlFault.get()}" }
        val row = checkNotNull(control.get()).put("phase", phase).put("generation", id).put("close", closing)
            .put("result_published", published).put("refusal", refusal)
        check(!published && refusal != null && row.getDouble("control_ms") < 50.0) { "Cancelled/closed result escaped its fence: $row" }
        check(if (closing) refusal in listOf("native PCM owner retired", "native recognizer retired")
              else refusal == "recognition generation cancelled") { "Different error is not cancellation evidence: $row" }
        if (!closing) {
            var refused = false; try { owner.begin(id) } catch (_: IllegalStateException) { refused = true }
            check(refused); row.put("stale_generation_refused", true)
            val recovery = trial(0, 2, item, enc, pred, joint, false)
            check(recovery.getBoolean("passed")); row.put("recovery", recovery)
        }
        return row
    }
    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        val modelDir = getExternalFilesDir(null) ?: error("Own directory unavailable")
        if (intent.getBooleanExtra("prepare_only", false)) { finish(); return }
        val runId = intent.getStringExtra("run_id") ?: error("Run id required")
        check(runId.matches(Regex("[a-f0-9]{32}")))
        outputDir = File(modelDir, "run-$runId"); check(outputDir.mkdir()) { "Run already exists" }
        Thread {
            val report = JSONObject().put("passed", false).put("voice_platform_qualified", false).put("run_id", runId)
                .put("cpu_fallback_allowed", false).put("accelerator_requested", "NPU").put("model", Build.MODEL)
                .put("fingerprint", Build.FINGERPRINT).put("pid", android.os.Process.myPid())
                .put("debuggable", applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0)
                .put("captured", captured).put("atol", 0.0005).put("rtol", 0.001)
            try {
                check(Build.MODEL == "Pixel 10 Pro") { "Wrong physical target" }
                val fixtureBytes = assets.open("length-fixture.json").readBytes()
                val fixture = JSONObject(String(fixtureBytes)); report.put("fixture_sha256", sha(fixtureBytes))
                val backend = fixture.optString("accelerator", "NPU")
                check(backend == "NPU" || backend == "GPU") { "Only explicit NPU/GPU backends are allowed" }
                val accelerator = if (backend == "GPU") Accelerator.GPU else Accelerator.NPU
                report.put("accelerator_requested", backend)
                if (backend == "GPU") check(fixture.getString("precision") == "fp32")
                val gpuOptions = fixture.optJSONObject("gpu_options") ?: JSONObject()
                check(gpuOptions.keys().asSequence().all { it == "shared_constant_graphs" }) { "Unknown GPU option" }
                val sharedGraphs = mutableSetOf<String>()
                if (gpuOptions.has("shared_constant_graphs")) {
                    check(backend == "GPU") { "Constant sharing requires GPU" }
                    val names = gpuOptions.getJSONArray("shared_constant_graphs")
                    check(names.length() in 1..3)
                    for (i in 0 until names.length()) {
                        val name = names.getString(i)
                        check(name in listOf("encoder", "predictor", "joint") && sharedGraphs.add(name)) { "Unknown or duplicate shared graph" }
                    }
                }
                report.put("gpu_options", gpuOptions)
                if (fixture.has("native_pcm")) {
                    check(backend == "GPU") { "This PCM composition is GPU-only" }
                    pcmOwner = NativeLengthPcm(floats(fixture.getJSONObject("native_pcm"), "mel"))
                    report.put("native_pcm", fixture.getJSONObject("native_pcm"))
                    report.put("native_frontend_edges", frontendEdges(fixture.getJSONObject("native_pcm").getJSONArray("edges")))
                }
                val paths = LinkedHashMap<String, File>(); val models = fixture.getJSONObject("models")
                for (name in listOf("encoder", "predictor", "joint")) {
                    val item = models.getJSONObject(name); val file = File(modelDir, item.getString("file"))
                    check(file.length() == item.getLong("bytes") && sha(file) == item.getString("sha256")) { "Model binding mismatch: $name" }; paths[name] = file
                }
                report.put("models", models)
                Environment.create(mapOf(Environment.Option.DispatchLibraryDir to applicationInfo.nativeLibraryDir)).use { env ->
                    report.put("available_accelerators", env.getAvailableAccelerators().toString())
                    check(env.getAvailableAccelerators().contains(accelerator)) { "$backend unavailable; refusing fallback" }
                    val graphs = LinkedHashMap<String, Graph>(); val loads = JSONObject()
                    report.put("loads", loads)
                    try {
                        for ((name, path) in paths) {
                            val start = System.nanoTime()
                            val options = CompiledModel.Options(accelerator)
                            if (backend == "GPU") options.gpuOptions = CompiledModel.GpuOptions(
                                precision = CompiledModel.GpuOptions.Precision.FP32,
                                constantTensorSharing = if (name in sharedGraphs) true else null)
                            Log.i("AII-PixelLength", "Loading $name on $backend")
                            val signatures = models.getJSONObject(name).getJSONArray("signatures")
                            val integer32 = name != "joint" && signatures.getJSONObject(0).getJSONArray("inputs")
                                .getJSONObject(if (name == "encoder") 1 else 0).getInt("type") == 2
                            val graph = Graph(CompiledModel.create(path.absolutePath, options, env), integer32) { if (generation > 0) checkpoint() }; graphs[name] = graph
                            loads.put(name, JSONObject().put("ms", (System.nanoTime() - start) / 1e6).put("pss_kib", Debug.getPss()))
                            check(signatures.length() == 1)
                            for (direction in listOf("inputs", "outputs")) {
                                val expected = signatures.getJSONObject(0).getJSONArray(direction)
                                check(expected.length() == (if (direction == "inputs") graph.input.size else graph.output.size))
                                for (i in 0 until expected.length()) {
                                    val tensor = expected.getJSONObject(i)
                                    val type = if (direction == "inputs") graph.model.getInputTensorType(tensor.getString("name"), "serving_default") else graph.model.getOutputTensorType(tensor.getString("name"), "serving_default")
                                    val actual = checkNotNull(type.layout).dimensions.toList()
                                    val shape = tensor.getJSONArray("shape"); check(actual == (0 until shape.length()).map { shape.getInt(it) }) { "Runtime tensor shape changed" }
                                }
                            }
                        }
                        if (backend == "GPU") {
                            // Compilation may propose residual CPU nodes before
                            // eventually refusing. The owner must inspect all
                            // actual delegation records before any model.run.
                            val ready = JSONObject().put("run_id", runId).put("pid", android.os.Process.myPid())
                                .put("fixture_sha256", sha(fixtureBytes))
                            publish("gpu-ready.json", ready)
                            val receipt = File(outputDir, "gpu-admission.json")
                            val deadline = System.nanoTime() + 30_000_000_000L
                            while (!receipt.exists() && System.nanoTime() < deadline) Thread.sleep(25)
                            check(receipt.exists()) { "GPU inference admission missing; refusing fallback" }
                            val admitted = JSONObject(receipt.readText())
                            check(admitted.getString("run_id") == runId && admitted.getInt("pid") == android.os.Process.myPid() &&
                                admitted.getString("fixture_sha256") == sha(fixtureBytes)) { "Foreign GPU admission" }
                            report.put("gpu_inference_admitted", true)
                        }
                        report.put("loads", loads); val trials = JSONArray(); report.put("trials", trials)
                        var allPassed = true; val cases = fixture.getJSONArray("cases")
                        repeat(2) { repeatIndex -> for (i in 0 until cases.length()) {
                            val row = trial(i, repeatIndex, cases.getJSONObject(i), graphs.getValue("encoder"), graphs.getValue("predictor"), graphs.getValue("joint"))
                            trials.put(row); allPassed = allPassed && row.getBoolean("passed"); Log.i("AII-PixelLength", row.toString())
                        } }
                        if (pcmOwner != null && allPassed) {
                            val controls = JSONArray(); report.put("native_controls", controls)
                            for (phase in listOf("encoder", "joint")) controls.put(controlProbe(cases.getJSONObject(0),
                                graphs.getValue("encoder"), graphs.getValue("predictor"), graphs.getValue("joint"), phase, false))
                            controls.put(controlProbe(cases.getJSONObject(0), graphs.getValue("encoder"),
                                graphs.getValue("predictor"), graphs.getValue("joint"), "encoder", true))
                            report.put("gpu_kernel_preemption_qualified", false)
                        }
                        report.put("passed", allPassed)
                    } finally { graphs.values.toList().reversed().forEach { it.close() } }
                }
            } catch (error: Throwable) {
                report.put("error", "${error.javaClass.name}: ${error.message}"); Log.e("AII-PixelLength", "Physical gate failed", error)
            } finally {
                pcmOwner?.close()
                if (activeTrial != null) report.put("active_trial", activeTrial)
                publish("result.json", report)
                Log.i("AII-PixelLength", report.toString()); runOnUiThread { finish() }
            }
        }.start()
    }
}
