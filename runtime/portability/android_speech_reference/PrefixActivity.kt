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
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import kotlin.math.max

// Executes the reusable native prefix adapter on actual GPU models. It is a
// recorded-input proof; optional full-session mode is still NOT physical audio.
class MainActivity : Activity() {
    private lateinit var output: File
    private fun sha(bytes: ByteArray)=MessageDigest.getInstance("SHA-256").digest(bytes).joinToString(""){"%02x".format(it)}
    private fun sha(file: File): String {
        val digest=MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input -> val buf=ByteArray(1048576)
            while(true){val n=input.read(buf);if(n<0)break;digest.update(buf,0,n)} }
        return digest.digest().joinToString(""){"%02x".format(it)}
    }
    private fun publish(name: String,value: JSONObject) {
        val temp=File(output,"$name.tmp");temp.writeText(value.toString(2));check(temp.renameTo(File(output,name)))
    }
    private fun floats(row: JSONObject): FloatArray {
        val raw=assets.open(row.getString("file")).readBytes()
        check(raw.size==row.getInt("bytes") && sha(raw)==row.getString("sha256"))
        val b=ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer()
        return FloatArray(b.remaining()){b.get()}.also {check(it.all { v->v.isFinite() })}
    }
    private class Graph(val model: CompiledModel):AutoCloseable {
        val input=try{model.createInputBuffers("serving_default")}catch(e:Throwable){model.close();throw e}
        val output=try{model.createOutputBuffers("serving_default")}catch(e:Throwable){input.forEach{it.close()};model.close();throw e}
        fun run(id:Long){NativePrefixRecognizer.checkpoint(id);model.run(input,output,"serving_default");NativePrefixRecognizer.checkpoint(id)}
        override fun close(){input.forEach{it.close()};output.forEach{it.close()};model.close()}
    }
    private fun argmax(x:FloatArray,start:Int,end:Int):Int {
        check(start>=0 && start<end && end<=x.size);var best=start
        for(i in start until end){check(x[i].isFinite());if(x[i]>x[best])best=i}
        return best-start
    }
    private inner class Decoder(val fixture:JSONObject,val root:File,val ready:JSONObject):NativePrefixRecognizer.Decoder {
        private var environment:Environment?=null
        private val graphs=LinkedHashMap<String,Graph>()
        private var thread=0L
        private var vocab=Array(8192){""}
        private var special=emptySet<Int>()
        val decodings=CopyOnWriteArrayList<JSONObject>()
        val last=AtomicReference<JSONObject>()
        val hold=AtomicReference<Pair<CountDownLatch,CountDownLatch>?>()
        val loads=JSONObject()
        var admitted=false
        @Volatile var closed=false
        override fun open() {
            thread=Thread.currentThread().id
            val tokenBytes=assets.open("tokenizer.json").readBytes()
            check(sha(tokenBytes)==fixture.getString("tokenizer_sha256"))
            val tokenizer=JSONObject(String(tokenBytes,Charsets.UTF_8))
            val decoder=tokenizer.getJSONObject("decoder")
            check(decoder.getString("type")=="Metaspace" && decoder.getString("replacement")=="▁" &&
                decoder.getString("prepend_scheme")=="always" && decoder.getBoolean("split"))
            val model=tokenizer.getJSONObject("model");check(model.getString("type")=="BPE")
            val values=model.getJSONObject("vocab");check(values.length()==8192)
            for(piece in values.keys()) {val id=values.getInt(piece);check(id in 0..8191 && vocab[id].isEmpty());vocab[id]=piece}
            check(vocab.all{it.isNotEmpty()})
            val added=tokenizer.getJSONArray("added_tokens");val skip=mutableSetOf<Int>()
            for(i in 0 until added.length()){val row=added.getJSONObject(i);if(row.getBoolean("special"))skip.add(row.getInt("id"))}
            special=skip
            val env=Environment.create(mapOf(Environment.Option.DispatchLibraryDir to applicationInfo.nativeLibraryDir));environment=env
            check(env.getAvailableAccelerators().contains(Accelerator.GPU)){"GPU unavailable; refusing fallback"}
            val models=fixture.getJSONObject("models")
            for(name in listOf("encoder","predictor","joint")) {
                val row=models.getJSONObject(name);val file=File(root,row.getString("file"))
                check(file.length()==row.getLong("bytes") && sha(file)==row.getString("sha256")){"Model binding differs"}
                val start=System.nanoTime()
                val options=CompiledModel.Options(Accelerator.GPU)
                options.gpuOptions=CompiledModel.GpuOptions(precision=CompiledModel.GpuOptions.Precision.FP32,
                    constantTensorSharing=if(name=="encoder")true else null)
                Log.i("AII-PixelPrefix","Loading $name on GPU")
                val graph=Graph(CompiledModel.create(file.absolutePath,options,env));graphs[name]=graph
                loads.put(name,JSONObject().put("ms",(System.nanoTime()-start)/1e6).put("pss_kib",Debug.getPss()))
                val signature=row.getJSONArray("signatures").getJSONObject(0)
                for(direction in listOf("inputs","outputs")) {
                    val expected=signature.getJSONArray(direction)
                    check(expected.length()==if(direction=="inputs")graph.input.size else graph.output.size)
                    for(i in 0 until expected.length()) {
                        val tensor=expected.getJSONObject(i)
                        val actual=if(direction=="inputs")graph.model.getInputTensorType(tensor.getString("name"),"serving_default")
                            else graph.model.getOutputTensorType(tensor.getString("name"),"serving_default")
                        val dims=tensor.getJSONArray("shape")
                        check(checkNotNull(actual.layout).dimensions.toList()==(0 until dims.length()).map{dims.getInt(it)})
                    }
                }
            }
            publish("gpu-ready.json",ready)
            val admission=File(output,"gpu-admission.json");val deadline=System.nanoTime()+30_000_000_000L
            while(!admission.exists() && System.nanoTime()<deadline)Thread.sleep(25)
            check(admission.exists()){"GPU delegation admission missing"}
            val got=JSONObject(admission.readText())
            check(got.getString("run_id")==ready.getString("run_id") && got.getInt("pid")==ready.getInt("pid") &&
                got.getString("fixture_sha256")==ready.getString("fixture_sha256")){"Foreign GPU admission"}
            admitted=true
        }
        override fun decode(features:FloatArray,validFrames:Int,cancellation:Long):ByteArray {
            check(Thread.currentThread().id==thread && admitted && !closed)
            check(features.size==256000 && validFrames in 2..2000)
            val enc=graphs.getValue("encoder");val pred=graphs.getValue("predictor");val joint=graphs.getValue("joint")
            val start=System.nanoTime();enc.input[0].writeFloat(features);enc.input[1].writeInt(intArrayOf(validFrames));enc.run(cancellation)
            val encoded=enc.output[0].readFloat();val length=enc.output[1].readInt()
            NativePrefixRecognizer.checkpoint(cancellation)
            val encoderMs=(System.nanoTime()-start)/1e6
            check(encoded.size==250*640 && encoded.all{it.isFinite()} && length.size==1 && length[0] in 1..250)
            hold.getAndSet(null)?.let{(entered,release)->entered.countDown();check(release.await(5,TimeUnit.SECONDS)){"held callback timed out"}}
            NativePrefixRecognizer.checkpoint(cancellation)
            var hidden=FloatArray(1280);var cell=FloatArray(1280)
            fun predict(token:Int):FloatArray {
                pred.input[0].writeInt(intArrayOf(token));pred.input[1].writeFloat(hidden);pred.input[2].writeFloat(cell);pred.run(cancellation)
                val result=pred.output[0].readFloat();hidden=pred.output[1].readFloat();cell=pred.output[2].readFloat()
                check(result.size==640 && hidden.size==1280 && cell.size==1280)
                check(result.all{it.isFinite()} && hidden.all{it.isFinite()} && cell.all{it.isFinite()})
                NativePrefixRecognizer.checkpoint(cancellation);return result
            }
            val tokens=mutableListOf<Int>();val steps=JSONArray();var prediction=predict(8192);var frame=0
            while(frame<length[0]) {
                check(steps.length()<2048){"decoder budget exhausted; refusing partial completion"}
                joint.input[0].writeFloat(encoded.copyOfRange(frame*640,(frame+1)*640));joint.input[1].writeFloat(prediction);joint.run(cancellation)
                val logits=joint.output[0].readFloat();check(logits.size==8198);NativePrefixRecognizer.checkpoint(cancellation)
                val token=argmax(logits,0,8193);val duration=argmax(logits,8193,8198)
                steps.put(JSONArray().put(frame).put(token).put(duration))
                if(token!=8192){tokens.add(token);prediction=predict(token)}
                frame+=if(token==8192 && duration==0)1 else duration
            }
            val pieces=tokens.filter{it !in special}.map{vocab[it]}
            val text=pieces.joinToString("").replace('▁',' ').removePrefix(" ")
            NativePrefixRecognizer.checkpoint(cancellation)
            val row=JSONObject().put("valid_input_frames",validFrames).put("encoder_frames",length[0])
                .put("tokens",JSONArray(tokens)).put("steps",steps).put("text",text)
                .put("encoder_output_ready_ms",encoderMs).put("decode_total_ms",(System.nanoTime()-start)/1e6)
                .put("cancellation",cancellation).put("owner_thread",thread).put("pss_kib",Debug.getPss())
            decodings.add(row);last.set(row)
            return text.toByteArray(Charsets.UTF_8)
        }
        override fun close(){
            check(Thread.currentThread().id==thread)
            graphs.values.reversed().forEach{it.close()};graphs.clear();environment?.close();environment=null;closed=true
        }
    }
    override fun onCreate(state:Bundle?) {
        super.onCreate(state)
        val root=checkNotNull(getExternalFilesDir(null))
        if(intent.getBooleanExtra("prepare_only",false)){finish();return}
        // Recover this diagnostic's own already-sealed evidence, without loading
        // models or rerunning a failed case. Originals are never modified.
        intent.getStringExtra("collect_run_id")?.let { previous ->
            check(previous.matches(Regex("[a-f0-9]{32}")))
            val source=File(root,"run-$previous")
            val terminal=JSONObject(File(source,"result.json").readText())
            check(terminal.getString("run_id")==previous)
            val collected=File(root,"collected-$previous");check(collected.mkdir())
            for(name in listOf("session.stdout","session.stderr")) {
                val expected=terminal.getJSONObject(name);val file=File(source,name)
                check(file.length()==expected.getLong("bytes") && file.length()<=4*1024*1024)
                val bytes=file.readBytes();check(sha(bytes)==expected.getString("sha256"))
                File(collected,name).writeBytes(bytes)
            }
            File(collected,"result.json").writeText(terminal.toString(2))
            finish();return
        }
        val runId=checkNotNull(intent.getStringExtra("run_id"));check(runId.matches(Regex("[a-f0-9]{32}")))
        output=File(root,"run-$runId");check(output.mkdir())
        Thread {
            val report=JSONObject().put("passed",false).put("voice_platform_qualified",false).put("physical_audio",false)
                .put("prefix_adapter",true).put("cpu_fallback_allowed",false).put("accelerator_requested","GPU")
                .put("run_id",runId).put("pid",android.os.Process.myPid()).put("fingerprint",Build.FINGERPRINT)
                .put("debuggable",applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE!=0).put("trials",JSONArray())
            var prefix:NativePrefixRecognizer?=null
            try {
                check(Build.MODEL=="Pixel 10 Pro")
                val raw=assets.open("length-fixture.json").readBytes();val fixture=JSONObject(String(raw,Charsets.UTF_8))
                check(fixture.getBoolean("prefix_adapter") && fixture.getString("accelerator")=="GPU")
                val fixtureSha=sha(raw);report.put("fixture_sha256",fixtureSha).put("gpu_options",fixture.getJSONObject("gpu_options"))
                val ready=JSONObject().put("run_id",runId).put("pid",android.os.Process.myPid()).put("fixture_sha256",fixtureSha)
                val decoder=Decoder(fixture,root,ready);report.put("loads",decoder.loads)
                val load=System.nanoTime()
                if(fixture.optBoolean("full_session")) {
                    val arguments=fixture.getJSONArray("session_arguments")
                    val values=Array(arguments.length()){i -> arguments.getString(i)
                        .replace("@ROOT@",root.absolutePath).replace("@OUTPUT@",output.absolutePath)}
                    report.put("full_session",true).put("decodings",JSONArray())
                    try {
                        val rc=NativeVoiceSession.run(decoder,floats(fixture.getJSONObject("native_pcm").getJSONObject("mel")),values,output.absolutePath)
                        report.put("session_exit_code",rc)
                        check(rc==0){"common five-model session failed; see retained native stdout/stderr"}
                        check(decoder.closed){"GPU owner not retired after session"}
                        report.put("model_owner_retired",true).put("passed",true)
                    } finally {
                        report.put("elapsed_ms",(System.nanoTime()-load)/1e6).put("gpu_inference_admitted",decoder.admitted)
                        report.put("decodings",JSONArray(decoder.decodings))
                        for(name in listOf("session.stdout","session.stderr")) {
                            val file=File(output,name)
                            if(file.exists())report.put(name,JSONObject().put("sha256",sha(file)).put("bytes",file.length()))
                        }
                    }
                    return@Thread // outer finally publishes the sole terminal record
                }
                prefix=NativePrefixRecognizer(decoder,floats(fixture.getJSONObject("native_pcm").getJSONObject("mel")))
                val engine=checkNotNull(prefix)
                report.put("load_ms",(System.nanoTime()-load)/1e6).put("gpu_inference_admitted",decoder.admitted)
                val cases=fixture.getJSONArray("cases");val trials=report.getJSONArray("trials")
                fun speech(item:JSONObject)=floats(item.getJSONObject("fields").getJSONObject("pcm"))
                fun push(pcm:FloatArray):Double {
                    var worst=0.0;var position=0
                    while(position<pcm.size){val n=minOf(241,pcm.size-position);val start=System.nanoTime()
                        engine.push(pcm,position,n);worst=max(worst,(System.nanoTime()-start)/1e6);position+=n}
                    return worst
                }
                fun trial(index:Int):JSONObject {
                    val item=cases.getJSONObject(index);val pcm=speech(item);engine.begin();val start=System.nanoTime()
                    val maxPush=push(pcm);val finalStart=System.nanoTime();val text=engine.finish()
                    val finalMs=(System.nanoTime()-finalStart)/1e6;val row=checkNotNull(decoder.last.get())
                    check(row.getInt("valid_input_frames")==pcm.size/160)
                    check(row.getJSONArray("tokens").toString()==item.getJSONArray("expected_tokens").toString()){"final tokens changed"}
                    check(row.getJSONArray("steps").toString()==item.getJSONArray("expected_steps").toString()){"final frame decisions changed"}
                    check(row.getString("text")==text){"stale result escaped adapter"}
                    engine.reset()
                    return JSONObject().put("case",index).put("input_samples",pcm.size).put("max_push_ms",maxPush)
                        .put("finish_ms",finalMs).put("elapsed_ms",(System.nanoTime()-start)/1e6).put("complete",true)
                        .put("passed",true).put("final",row)
                }
                for(i in 0 until cases.length())trials.put(trial(i))
                // Actual GPU callback held after completed encoder readback.
                val held=CountDownLatch(1);val release=CountDownLatch(1);decoder.hold.set(Pair(held,release));engine.begin()
                val finishOutcome=AtomicReference<String>();val returned=CountDownLatch(1)
                try {
                    push(speech(cases.getJSONObject(0)));check(held.await(15,TimeUnit.SECONDS)){"no real GPU callback held"}
                    val finalThread=Thread{try{engine.finish();finishOutcome.set("unexpected final")}
                        catch(e:IllegalStateException){finishOutcome.set(e.message)}finally{returned.countDown()}}
                    finalThread.start();val start=System.nanoTime();engine.cancel();val cancelMs=(System.nanoTime()-start)/1e6
                    check(release.count==1L && returned.await(100,TimeUnit.MILLISECONDS)){"cancel waited behind final inference"}
                    check(cancelMs<50 && finishOutcome.get()=="prefix recognition cancelled"){"wrong cancellation outcome"}
                    release.countDown();finalThread.join(1000);check(!finalThread.isAlive);engine.reset()
                    report.put("control",JSONObject().put("cancel_ms",cancelMs).put("returned_while_held",true)
                        .put("refusal",finishOutcome.get()).put("recovery",trial(0)))
                } finally {release.countDown()}
                engine.close();prefix=null
                check(decoder.closed);report.put("model_owner_retired",true).put("decodings",JSONArray(decoder.decodings))
                report.put("gpu_kernel_preemption_qualified",false).put("maximum_utterance_samples",320000).put("passed",true)
            }catch(e:Throwable){report.put("error",e.stackTraceToString());Log.e("AII-PixelPrefix","prefix proof failed",e)}
            finally {
                try{prefix?.close()}catch(e:Throwable){report.put("passed",false).put("retirement_error",e.toString())}
                publish("result.json",report);runOnUiThread{finish()}
            }
        }.start()
    }
}
