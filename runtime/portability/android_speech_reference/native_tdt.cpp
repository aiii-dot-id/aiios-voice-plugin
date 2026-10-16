// Project-owned model and buffer lifetime through LiteRT's public C API.
// No Kotlin/JniHandle representation, private field access or borrowed pointer.
// Inference has one owner; generation cancellation never waits for that owner.
// The JNI registry pins object lifetime during calls without borrowing handles.
// PCM streaming and host delivery fences remain a separate integration gate.
#include <jni.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstring>
#include <memory>
#include <map>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <vector>
#include "native_generation_gate.h"
#include "native_pcm_frontend.h"
#include "native_pcm_windows.h"
#include "litert/c/litert_compiled_model.h"
#include "litert/c/litert_environment.h"
#include "litert/c/litert_model.h"
#include "litert/c/litert_options.h"
#include "litert/c/litert_tensor_buffer.h"

namespace {
using Buffer = std::shared_ptr<std::remove_pointer_t<LiteRtTensorBuffer>>;
using Buffers = std::vector<Buffer>;
using Clock = std::chrono::steady_clock;
void require(bool yes, const char* why) { if (!yes) throw std::runtime_error(why); }
void check(LiteRtStatus result, const char* why) {if(result!=kLiteRtStatusOk)throw std::runtime_error(std::string(why)+": "+std::to_string(result));}
template<class H,void(*Destroy)(H)> struct Owned {
    H value=nullptr;
    Owned()=default;Owned(const Owned&)=delete;Owned& operator=(const Owned&)=delete;
    ~Owned(){if(value)Destroy(value);}
};
double elapsed(Clock::time_point begin) {
    return std::chrono::duration<double,std::milli>(Clock::now()-begin).count();
}
size_t count(const Buffer& b, LiteRtElementType kind) {
    LiteRtRankedTensorType t{};check(LiteRtGetTensorBufferTensorType(b.get(),&t),"tensor type unavailable");
    require(t.element_type==kind,"tensor element type changed");
    require(t.layout.rank>0 && t.layout.rank<=8,"tensor rank changed");
    size_t n=1;for(unsigned i=0;i<t.layout.rank;++i) {
        require(t.layout.dimensions[i]>0 && size_t(t.layout.dimensions[i])<=100000000/n,"tensor element bound exceeded");
        n*=t.layout.dimensions[i];
    }
    return n;
}
template<class T> void write(Buffer& b,const std::vector<T>& values) {
    auto type = sizeof(T)==sizeof(float) && std::is_same_v<T,float>
        ? kLiteRtElementTypeFloat32 : kLiteRtElementTypeInt32;
    require(count(b,type)==values.size(),"write extent differs");
    void* p=nullptr;check(LiteRtLockTensorBuffer(b.get(),&p,kLiteRtTensorBufferLockModeWrite),"write lock failed");
    std::memcpy(p,values.data(),values.size()*sizeof(T));
    check(LiteRtUnlockTensorBuffer(b.get()),"write unlock failed");
}
Buffers refs(std::initializer_list<Buffer*> buffers) {
    Buffers result; result.reserve(buffers.size());
    for(auto* b:buffers)result.push_back(*b);
    return result;
}
// Model logits must be finite. First maximum wins, as in the original decoder.
int argmax(const std::vector<float>& values,int first,int last) {
    require(first>=0 && first<last && size_t(last)<=values.size(),"argmax bounds");
    int best=first;
    for(int i=first;i<last;++i) {
        require(std::isfinite(values[i]),"nonfinite model logits");
        if(values[i]>values[best] || (values[i]==0 && values[best]==0 &&
           !std::signbit(values[i]) && std::signbit(values[best])))best=i;
    }
    return best-first;
}
struct NativeModel {
    Owned<LiteRtEnvironment,LiteRtDestroyEnvironment> env;
    Owned<LiteRtOptions,LiteRtDestroyOptions> options;
    Owned<LiteRtModel,LiteRtDestroyModel> model;
    Owned<LiteRtCompiledModel,LiteRtDestroyCompiledModel> compiled;
    std::map<std::string,std::pair<LiteRtParamIndex,LiteRtSignature>> signatures;
    NativeModel(const std::string& path,const std::string& dir) {
        LiteRtEnvOption option{};option.tag=kLiteRtEnvOptionTagDispatchLibraryDir;
        option.value.type=kLiteRtAnyTypeString;option.value.str_value=dir.c_str();
        check(LiteRtCreateEnvironment(1,&option,&env.value),"environment creation failed");
        check(LiteRtCreateOptions(&options.value),"options creation failed");
        check(LiteRtSetOptionsHardwareAccelerators(options.value,kLiteRtHwAcceleratorNpu),"NPU selection failed");
        check(LiteRtCreateModelFromFile(env.value,path.c_str(),&model.value),"model load failed");
        check(LiteRtCreateCompiledModel(env.value,model.value,options.value,&compiled.value),"NPU compilation failed");
        LiteRtParamIndex n=0;check(LiteRtGetNumModelSignatures(model.value,&n),"signature count failed");
        require(n==3,"signature count changed");
        for(LiteRtParamIndex i=0;i<n;++i) {
            LiteRtSignature signature=nullptr;const char* key=nullptr;
            check(LiteRtGetModelSignature(model.value,i,&signature),"signature unavailable");
            check(LiteRtGetSignatureKey(signature,&key),"signature name unavailable");
            require(key && signatures.emplace(key,std::make_pair(i,signature)).second,"duplicate signature");
        }
    }
    Buffers buffers(const std::string& name,bool input) {
        auto [index,signature]=signatures.at(name);LiteRtParamIndex n=0;
        check(input?LiteRtGetNumSignatureInputs(signature,&n):LiteRtGetNumSignatureOutputs(signature,&n),"buffer count failed");
        require(n>0 && n<=4,"buffer count changed");Buffers result;
        for(LiteRtParamIndex i=0;i<n;++i) {
            LiteRtTensor tensor=nullptr;LiteRtRankedTensorType type{};LiteRtTensorBufferRequirements requirements=nullptr;
            check(input?LiteRtGetSignatureInputTensorByIndex(signature,i,&tensor):LiteRtGetSignatureOutputTensorByIndex(signature,i,&tensor),"tensor unavailable");
            check(LiteRtGetRankedTensorType(tensor,&type),"ranked type unavailable");
            check(input?LiteRtGetCompiledModelInputBufferRequirements(compiled.value,index,i,&requirements):
                LiteRtGetCompiledModelOutputBufferRequirements(compiled.value,index,i,&requirements),"buffer requirements unavailable");
            LiteRtTensorBuffer b=nullptr;
            check(LiteRtCreateManagedTensorBufferFromRequirements(env.value,&type,requirements,&b),"owned buffer allocation failed");
            result.emplace_back(b,LiteRtDestroyTensorBuffer);
        }
        return result;
    }
    void run(const std::string& signature,const Buffers& in,const Buffers& out) {
        std::vector<LiteRtTensorBuffer> inputs,outputs;
        for(const auto& b:in)inputs.push_back(b.get());for(const auto& b:out)outputs.push_back(b.get());
        check(LiteRtRunCompiledModel(compiled.value,signatures.at(signature).first,
            inputs.size(),inputs.data(),outputs.size(),outputs.data()),"NPU inference failed");
    }
};
struct NativeTdt {
    // Declaration order makes every buffer retire before model and environment.
    NativeModel model;
    Buffers ei,eo,di,dout,si,sout;
    Buffers in[2][2],out[2][2];
    size_t frames,tokens,logits,states;
    std::vector<float> windows;
    aii::voice::pixel::PcmFrontend frontend;
    aii::voice::pixel::GenerationGate gate;
    std::mutex inference,holdMutex;
    std::condition_variable holdChanged;
    uint64_t released=0,published=0;
    bool closing=false;
    std::atomic<uint64_t> active{0};
    std::atomic<int> completedCalls{0};
    std::atomic<int> completedWindows{0};
    std::atomic<bool> paused{false};
    void cancel(uint64_t generation) noexcept {gate.cancel(generation);}
    void close() noexcept {
        gate.close();
        {std::lock_guard<std::mutex> guard(holdMutex);closing=true;}
        holdChanged.notify_all();
    }
    void release(uint64_t generation) {
        std::lock_guard<std::mutex> guard(holdMutex);
        released=std::max(released,generation);holdChanged.notify_all();
    }
    void diagnostic_hold(uint64_t generation) {
        std::unique_lock<std::mutex> guard(holdMutex);paused=true;
        const bool signalled=holdChanged.wait_for(guard,std::chrono::seconds(5),[&]{return closing || released>=generation;});
        paused=false;require(signalled,"diagnostic hold release timed out");
    }
    NativeTdt(const std::string& path,const std::string& dir):model(path,dir),
        ei(model.buffers("encode",true)),eo(model.buffers("encode",false)),
        di(model.buffers("decode",true)),dout(model.buffers("decode",false)),
        si(model.buffers("decode_1",true)),sout(model.buffers("decode_1",false)) {
        require(ei.size()==1 && eo.size()==1 && di.size()==4 && dout.size()==3 &&
            si.size()==4 && sout.size()==3,"signature arity changed");
        require(count(ei[0],kLiteRtElementTypeFloat32)==64000,"feature extent changed");
        frames=count(di[0],kLiteRtElementTypeFloat32)/1024;
        tokens=count(di[1],kLiteRtElementTypeInt32);
        require(frames==63 && tokens>1 && tokens<=128,"decoder input extent changed");
        logits=count(dout[0],kLiteRtElementTypeFloat32)/(frames*tokens);
        require(logits==8198 && count(dout[0],kLiteRtElementTypeFloat32)==frames*tokens*logits,
            "stateless logit extent changed");
        require(count(sout[0],kLiteRtElementTypeFloat32)==frames*logits &&
            count(si[1],kLiteRtElementTypeInt32)==1,"stateful logit extent changed");
        states=count(di[2],kLiteRtElementTypeFloat32);
        require(states<=1000000 && count(di[3],kLiteRtElementTypeFloat32)==states &&
            count(dout[1],kLiteRtElementTypeFloat32)==states &&
            count(dout[2],kLiteRtElementTypeFloat32)==states,"state extent changed");
        for(int mode=0;mode<2;++mode)for(int bank=0;bank<2;++bank) {
            auto& inputStates=bank?dout:di; auto& outputStates=bank?di:dout;
            const int inputOffset=bank?1:2,outputOffset=bank?2:1;
            in[mode][bank]=refs({&eo[0],mode?&si[1]:&di[1],
                &inputStates[inputOffset],&inputStates[inputOffset+1]});
            out[mode][bank]=refs({mode?&sout[0]:&dout[0],
                &outputStates[outputOffset],&outputStates[outputOffset+1]});
        }
    }
    struct Retire {std::atomic<uint64_t>& active;~Retire(){active=0;}};
    static void validateInput(const std::vector<float>& input,int holdAfter,bool pcm) {
        require(holdAfter>=0 && holdAfter<=512,"invalid diagnostic hold");
        if(pcm)aii::voice::pixel::PcmFrontend::validate(input);
        else require(input.size()==64000 && std::all_of(input.begin(),input.end(),
            [](float v){return std::isfinite(v);}),"finite exact features required");
    }
    std::string recognize(uint64_t generation,const std::vector<float>& input,bool capture,int holdAfter,bool pcm=false) {
        std::unique_lock<std::mutex> work(inference,std::try_to_lock);
        require(work.owns_lock(),"recognition owner busy");
        validateInput(input,holdAfter,pcm);
        gate.admit(generation);active=generation;completedWindows=0;Retire retire{active};
        return recognizeLocked(generation,input,capture,holdAfter,pcm);
    }
    // Called only while the single inference owner is held and generation has
    // been admitted. A multi-window operation retains that ownership and fence.
    std::string recognizeLocked(uint64_t generation,const std::vector<float>& input,bool capture,int holdAfter,bool pcm) {
        validateInput(input,holdAfter,pcm);gate.check(generation);completedCalls=0;published=0;
        windows.clear();if(capture)windows.reserve(512*logits);
        else std::vector<float>().swap(windows);
        gate.check(generation);
        auto frontBegin=Clock::now();
        std::vector<float> processed;
        if(pcm)processed=frontend.process(input,[&]{gate.check(generation);});
        const auto& features=pcm?processed:input;
        const double frontendMs=pcm?elapsed(frontBegin):0;
        write(ei[0],features); auto start=Clock::now();
        model.run("encode",ei,eo);
        gate.check(generation);
        const double encodeMs=elapsed(start);
        write(di[2],std::vector<float>(states,0));write(di[3],std::vector<float>(states,0));
        int bank=0,mode=0,tokenIndex=0,timeIndex=0,steps=0;
        std::vector<int32_t> ids(tokens,0);ids[0]=8192;
        std::vector<float> window(logits); std::vector<std::pair<int,int>> emitted;
        double runMs=0,readMs=0;start=Clock::now();
        while(size_t(timeIndex)<frames) {
            gate.check(generation);
            require(++steps<=512,"TDT step budget exhausted");
            write(mode?si[1]:di[1],ids);
            auto phase=Clock::now();
            model.run(mode?"decode_1":"decode",in[mode][bank],out[mode][bank]);
            completedCalls=steps;
            if(holdAfter==steps)diagnostic_hold(generation);
            gate.check(generation);
            runMs+=elapsed(phase);phase=Clock::now();
            auto& output=mode?sout[0]:dout[0];
            const size_t offset=(timeIndex*ids.size()+tokenIndex)*logits;
            require(offset+logits<=count(output,kLiteRtElementTypeFloat32),"logit window extent differs");
            void* locked=nullptr;check(LiteRtLockTensorBuffer(output.get(),&locked,kLiteRtTensorBufferLockModeRead),"logit lock failed");
            std::memcpy(window.data(),static_cast<float*>(locked)+offset,logits*sizeof(float));
            check(LiteRtUnlockTensorBuffer(output.get()),"logit unlock failed");readMs+=elapsed(phase);
            if(capture)windows.insert(windows.end(),window.begin(),window.end());
            const int token=argmax(window,0,8193),duration=argmax(window,8193,8198);
            if(token!=8192) {
                emitted.emplace_back(token,timeIndex);
                if(!mode && ++tokenIndex==int(tokens)) {mode=1;tokenIndex=0;ids.assign(1,0);}
                ids[tokenIndex]=token;
            }
            timeIndex+=(duration==0 && token==8192)?1:duration;
            if(mode && token!=8192)bank=1-bank;
        }
        std::ostringstream json;
        json<<"{\"generation\":"<<generation<<",\"encode_ms\":"<<encodeMs<<",\"decode_ms\":"<<elapsed(start)
            <<",\"input_samples\":"<<(pcm?input.size():0)<<",\"frontend_ms\":"<<frontendMs
            <<",\"model_run_ms\":"<<runMs<<",\"read_logits_ms\":"<<readMs
            <<",\"model_calls\":"<<steps<<",\"logit_bytes_copied\":"<<steps*logits*4
            <<",\"diagnostic_retained_bytes\":"<<windows.size()*4
            <<",\"complete\":true,\"tokens\":[";
        for(size_t i=0;i<emitted.size();++i) {
            if(i)json<<',';json<<'['<<emitted[i].first<<','<<emitted[i].second<<']';
        }
        json<<"]}";gate.check(generation);published=generation;return json.str();
    }
    // Recorded-input adapter for the same PcmWindows used by a future live
    // recognizer. Full input retention/comparisons here are diagnostic only.
    std::string sequence(uint64_t generation,const std::vector<float>& pcm,const std::vector<int>& packets,int holdWindow) {
        std::unique_lock<std::mutex> work(inference,std::try_to_lock);require(work.owns_lock(),"recognition owner busy");
        require(pcm.size()>=258 && pcm.size()<=16000*60,"recorded sequence extent");
        require(!packets.empty() && packets.size()<=64 && std::all_of(packets.begin(),packets.end(),[](int n){return n>0 && n<=32000;}),"packet pattern invalid");
        require(std::all_of(pcm.begin(),pcm.end(),[](float v){return std::isfinite(v);}) && holdWindow>=0 && holdWindow<=60,"sequence input invalid");
        gate.admit(generation);active=generation;completedWindows=0;Retire retire{active};
        aii::voice::pixel::PcmWindows buffer;std::ostringstream result;
        result<<"{\"generation\":"<<generation<<",\"windows\":[";
        auto consume=[&](const aii::voice::pixel::PcmWindows::Window& w){
            gate.check(generation);
            require(w.end<=pcm.size() && w.pcm.size()==w.end-w.start &&
                std::memcmp(w.pcm.data(),pcm.data()+w.start,w.pcm.size()*sizeof(float))==0,"window PCM bits differ from source");
            const auto decoded=recognizeLocked(generation,w.pcm,false,0,true);
            if(completedWindows.load())result<<',';
            result<<"{\"start\":"<<w.start<<",\"end\":"<<w.end<<",\"pcm_bits_identical\":true,\"result\":"<<decoded<<'}';
            const auto done=++completedWindows;
            if(done==holdWindow)diagnostic_hold(generation);
            gate.check(generation);
        };
        size_t offset=0,index=0;
        while(offset<pcm.size()){
            gate.check(generation);const auto n=std::min<size_t>(packets[index++%packets.size()],pcm.size()-offset);
            buffer.accept(offset,pcm.data()+offset,n,consume);offset+=n;
        }
        buffer.finish(pcm.size(),consume);buffer.finish(pcm.size(),consume);
        gate.check(generation);
        result<<"],\"source_samples\":"<<buffer.received()<<",\"emitted_through\":"<<buffer.emitted()
            <<",\"retained_samples\":"<<buffer.retained()<<",\"complete\":true}";
        return result.str();
    }
    std::vector<float> trace() {
        std::unique_lock<std::mutex> work(inference,std::try_to_lock);
        require(work.owns_lock(),"recognition owner busy");
        require(published!=0,"no completed recognition trace");gate.check(published);
        return windows;
    }
};
std::string text(JNIEnv* e,jstring s) {
    require(s!=nullptr,"path missing"); const char* p=e->GetStringUTFChars(s,nullptr);
    require(p!=nullptr,"path conversion failed"); std::string r(p);e->ReleaseStringUTFChars(s,p);
    require(!r.empty() && r.size()<4096,"path bound exceeded");return r;
}
// Only this registry's opaque identifiers cross JNI. Every call pins a shared
// owner, so a concurrent retire cannot free an executing recognizer. Destruction
// happens outside the registry lock and after the last inference/control caller.
std::mutex registryMutex;
std::map<jlong,std::shared_ptr<NativeTdt>> registry;
jlong nextOwner=0;
std::shared_ptr<NativeTdt> owner(jlong h) {
    std::lock_guard<std::mutex> guard(registryMutex);
    auto found=registry.find(h);require(found!=registry.end(),"native recognizer retired");return found->second;
}
void error(JNIEnv* e,const std::exception& x) {e->ThrowNew(e->FindClass("java/lang/IllegalStateException"),x.what());}
}
extern "C" JNIEXPORT jlong JNICALL Java_com_google_ai_edge_examples_asr_NativeSpeech_open(JNIEnv* e,jclass,jstring p,jstring d) {
    try {auto instance=std::make_shared<NativeTdt>(text(e,p),text(e,d));
        std::lock_guard<std::mutex> guard(registryMutex);
        require(nextOwner<INT64_MAX,"native owner identifiers exhausted");
        const auto id=++nextOwner;registry.emplace(id,std::move(instance));return id;
    }catch(const std::exception& x){error(e,x);return 0;}
}
extern "C" JNIEXPORT jstring JNICALL Java_com_google_ai_edge_examples_asr_NativeSpeech_recognize0(JNIEnv* e,jclass,jlong h,jlong generation,jfloatArray a,jboolean capture,jint holdAfter) {
    try {require(a && e->GetArrayLength(a)==64000,"feature extent invalid");std::vector<float> f(64000);
        require(generation>0,"positive generation required");auto instance=owner(h);
        e->GetFloatArrayRegion(a,0,64000,f.data());require(!e->ExceptionCheck(),"feature copy failed");
        return e->NewStringUTF(instance->recognize(generation,f,capture,holdAfter).c_str());}catch(const std::exception& x){error(e,x);return nullptr;}
}
extern "C" JNIEXPORT jstring JNICALL Java_com_google_ai_edge_examples_asr_NativeSpeech_pcm0(JNIEnv* e,jclass,jlong h,jlong generation,jfloatArray a) {
    try {require(a,"PCM missing");const auto n=e->GetArrayLength(a);require(n>=258 && n<=80000,"PCM requires uncut 258..80000 samples at 16 kHz");
        require(generation>0,"positive generation required");auto instance=owner(h);std::vector<float> pcm(n);
        e->GetFloatArrayRegion(a,0,n,pcm.data());require(!e->ExceptionCheck(),"PCM copy failed");
        return e->NewStringUTF(instance->recognize(generation,pcm,false,0,true).c_str());
    }catch(const std::exception& x){error(e,x);return nullptr;}
}
extern "C" JNIEXPORT jfloatArray JNICALL Java_com_google_ai_edge_examples_asr_NativeSpeech_frontend0(JNIEnv* e,jclass,jfloatArray a) {
    try {require(a,"PCM missing");const auto n=e->GetArrayLength(a);require(n>=258 && n<=80000,"PCM requires uncut 258..80000 samples at 16 kHz");
        std::vector<float> pcm(n);e->GetFloatArrayRegion(a,0,n,pcm.data());require(!e->ExceptionCheck(),"PCM copy failed");
        auto features=aii::voice::pixel::PcmFrontend().process(pcm);auto out=e->NewFloatArray(features.size());
        if(out)e->SetFloatArrayRegion(out,0,features.size(),features.data());return out;
    }catch(const std::exception& x){error(e,x);return nullptr;}
}
extern "C" JNIEXPORT jstring JNICALL Java_com_google_ai_edge_examples_asr_NativeSpeech_sequence0(JNIEnv* e,jclass,jlong h,jlong generation,jfloatArray a,jintArray sizes,jint holdWindow) {
    try {require(a && sizes && generation>0,"sequence arguments missing");const auto n=e->GetArrayLength(a),p=e->GetArrayLength(sizes);
        require(n>=258 && n<=16000*60 && p>0 && p<=64,"recorded sequence extent");
        auto instance=owner(h);std::vector<float> pcm(n);std::vector<int> packets(p);
        e->GetFloatArrayRegion(a,0,n,pcm.data());e->GetIntArrayRegion(sizes,0,p,packets.data());require(!e->ExceptionCheck(),"sequence copy failed");
        return e->NewStringUTF(instance->sequence(generation,pcm,packets,holdWindow).c_str());
    }catch(const std::exception& x){error(e,x);return nullptr;}
}
extern "C" JNIEXPORT jfloatArray JNICALL Java_com_google_ai_edge_examples_asr_NativeSpeech_trace0(JNIEnv* e,jclass,jlong h) {
    try {auto w=owner(h)->trace();auto a=e->NewFloatArray(w.size());if(a)e->SetFloatArrayRegion(a,0,w.size(),w.data());return a;}
    catch(const std::exception& x){error(e,x);return nullptr;}
}
extern "C" JNIEXPORT void JNICALL Java_com_google_ai_edge_examples_asr_NativeSpeech_cancel0(JNIEnv* e,jclass,jlong h,jlong generation) {
    try {require(generation>0,"positive generation required");owner(h)->cancel(generation);}
    catch(const std::exception& x){error(e,x);}
}
extern "C" JNIEXPORT void JNICALL Java_com_google_ai_edge_examples_asr_NativeSpeech_release0(JNIEnv* e,jclass,jlong h,jlong generation) {
    try {require(generation>0,"positive generation required");owner(h)->release(generation);}
    catch(const std::exception& x){error(e,x);}
}
extern "C" JNIEXPORT jstring JNICALL Java_com_google_ai_edge_examples_asr_NativeSpeech_status0(JNIEnv* e,jclass,jlong h) {
    try {auto m=owner(h);std::ostringstream s;s<<"{\"active\":"<<m->active.load()<<",\"completed_calls\":"<<m->completedCalls.load()
        <<",\"completed_windows\":"<<m->completedWindows.load()<<",\"paused\":"<<(m->paused.load()?"true":"false")<<'}';return e->NewStringUTF(s.str().c_str());}
    catch(const std::exception& x){error(e,x);return nullptr;}
}
extern "C" JNIEXPORT void JNICALL Java_com_google_ai_edge_examples_asr_NativeSpeech_retire(JNIEnv*,jclass,jlong h) {
    std::shared_ptr<NativeTdt> instance;
    {std::lock_guard<std::mutex> guard(registryMutex);auto found=registry.find(h);
     if(found==registry.end())return;instance=std::move(found->second);registry.erase(found);}
    instance->close();
}
