#include <jni.h>
#include <atomic>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include "prefix_recognizer.h"
#include "prefix_jni.h"
#include "length_pcm_frontend.h"

namespace {
using namespace aii::voice;
std::mutex cancellation_mutex;
std::map<jlong,const std::atomic<bool>*> cancellations;
jlong next_cancellation=0;
struct Ticket {
  jlong id;
  explicit Ticket(const std::atomic<bool>& token) {
    std::lock_guard<std::mutex> lock(cancellation_mutex);
    if(next_cancellation==INT64_MAX)throw std::runtime_error("cancellation ids exhausted");
    id=++next_cancellation;cancellations.emplace(id,&token);
  }
  ~Ticket() {std::lock_guard<std::mutex> lock(cancellation_mutex);cancellations.erase(id);}
};
void java_error(JNIEnv* env) {
  if(!env->ExceptionCheck())return;
  const auto error=env->ExceptionOccurred();env->ExceptionClear();
  const auto cancelled=env->FindClass("java/util/concurrent/CancellationException");
  const bool is_cancelled=cancelled && env->IsInstanceOf(error,cancelled);
  if(cancelled)env->DeleteLocalRef(cancelled);
  if(!is_cancelled){env->Throw(error);env->ExceptionDescribe();env->ExceptionClear();}
  env->DeleteLocalRef(error);
  if(is_cancelled)throw Cancelled("accelerator prefix cancelled");
  throw std::runtime_error("Java accelerator callback failed; see retained device exception");
}
struct JavaDecoder:PrefixDecoder {
  JavaVM* vm=nullptr;
  JNIEnv* env=nullptr;
  jobject callback=nullptr;
  jmethodID open_method=nullptr,decode_method=nullptr,close_method=nullptr;
  bool attached=false;
  pixel::LengthPcmFrontend frontend;
  std::shared_ptr<std::atomic<bool>> lifecycle;
  JavaDecoder(JNIEnv* caller,jobject value,std::vector<float> mel,std::shared_ptr<std::atomic<bool>> state)
      :frontend(std::move(mel)),lifecycle(std::move(state)) {
    if(!value || caller->GetJavaVM(&vm)!=JNI_OK)throw std::invalid_argument("Java model owner required");
    auto cls=caller->GetObjectClass(value);
    open_method=caller->GetMethodID(cls,"open","()V");java_error(caller);
    decode_method=caller->GetMethodID(cls,"decode","([FIJ)[B");java_error(caller);
    close_method=caller->GetMethodID(cls,"close","()V");java_error(caller);
    caller->DeleteLocalRef(cls);java_error(caller);
    callback=caller->NewGlobalRef(value);
    if(!callback)throw std::runtime_error("cannot pin Java model owner");
  }
  void open() override {
    if(vm->AttachCurrentThread(&env,nullptr)!=JNI_OK)throw std::runtime_error("cannot attach inference owner");
    attached=true;env->CallVoidMethod(callback,open_method);java_error(env);
  }
  std::string decode(const std::vector<float>& pcm,const std::atomic<bool>& cancel) override {
    auto check=[&]{if(cancel.load())throw Cancelled("accelerator prefix cancelled");};
    check();const auto features=frontend.process(pcm,check);Ticket ticket(cancel);
    if(env->PushLocalFrame(8)!=JNI_OK)throw std::runtime_error("JNI local frame unavailable");
    try {
      auto values=env->NewFloatArray(features.size());
      if(!values)throw std::runtime_error("JNI feature allocation failed");
      env->SetFloatArrayRegion(values,0,features.size(),features.data());java_error(env);check();
      auto raw=static_cast<jbyteArray>(env->CallObjectMethod(callback,decode_method,values,jint(pcm.size()/160),ticket.id));
      java_error(env);check();if(!raw)throw std::runtime_error("missing accelerator transcript");
      const auto n=env->GetArrayLength(raw);
      if(n<0 || n>131071)throw std::runtime_error("accelerator transcript exceeds bound");
      std::string text(size_t(n),'\0');env->GetByteArrayRegion(raw,0,n,reinterpret_cast<jbyte*>(text.data()));
      java_error(env);check();env->PopLocalFrame(nullptr);return text;
    } catch(...) {env->PopLocalFrame(nullptr);throw;}
  }
  ~JavaDecoder() override {
    // This destructor is run by PrefixRecognizer on the inference thread.
    if(attached) {
      env->CallVoidMethod(callback,close_method);
      if(env->ExceptionCheck()){env->ExceptionDescribe();env->ExceptionClear();*lifecycle=true;}
      env->DeleteGlobalRef(callback);vm->DetachCurrentThread();
    } else if(callback) {
      JNIEnv* cleanup=nullptr;
      if(vm->AttachCurrentThread(&cleanup,nullptr)==JNI_OK){cleanup->DeleteGlobalRef(callback);vm->DetachCurrentThread();}
      *lifecycle=true;
    }
  }
};
struct Owner {
  std::mutex calls;
  std::atomic<bool> retired{false};
  std::shared_ptr<std::atomic<bool>> lifecycle=std::make_shared<std::atomic<bool>>(false);
  std::unique_ptr<Recognizer> recognizer;
  Owner(JNIEnv* env,jobject decoder,std::vector<float> mel) {
    recognizer=make_java_prefix(env,decoder,std::move(mel),lifecycle);
    recognizer->open();
  }
  void check() {if(retired.load())throw std::runtime_error("native prefix owner retired");}
};
std::mutex registry_mutex;
std::map<jlong,std::shared_ptr<Owner>> registry;
jlong next_owner=0;
std::shared_ptr<Owner> owner(jlong id) {
  std::lock_guard<std::mutex> lock(registry_mutex);auto found=registry.find(id);
  if(found==registry.end())throw std::runtime_error("native prefix owner retired");return found->second;
}
void fail(JNIEnv* env,const std::exception& e) {
  if(!env->ExceptionCheck())env->ThrowNew(env->FindClass("java/lang/IllegalStateException"),e.what());
}
jbyteArray string_bytes(JNIEnv* env,const std::string& text) {
  auto result=env->NewByteArray(text.size());
  if(result)env->SetByteArrayRegion(result,0,text.size(),reinterpret_cast<const jbyte*>(text.data()));return result;
}
}
extern "C" JNIEXPORT jlong JNICALL Java_com_google_ai_edge_examples_asr_NativePrefixRecognizer_create(JNIEnv* env,jclass,jobject decoder,jfloatArray mel) {
  try {
    if(!mel || env->GetArrayLength(mel)!=128*257)throw std::invalid_argument("exact model mel coefficients required");
    std::vector<float> values(128*257);env->GetFloatArrayRegion(mel,0,values.size(),values.data());java_error(env);
    auto instance=std::make_shared<Owner>(env,decoder,std::move(values));
    std::lock_guard<std::mutex> lock(registry_mutex);
    if(next_owner==INT64_MAX)throw std::runtime_error("prefix owner ids exhausted");
    const auto id=++next_owner;registry.emplace(id,std::move(instance));return id;
  } catch(const std::exception& e){fail(env,e);return 0;}
}
extern "C" JNIEXPORT void JNICALL Java_com_google_ai_edge_examples_asr_NativePrefixRecognizer_checkpoint(JNIEnv* env,jclass,jlong id) {
  std::lock_guard<std::mutex> lock(cancellation_mutex);
  auto found=cancellations.find(id);
  if(found==cancellations.end() || found->second->load())
    env->ThrowNew(env->FindClass("java/util/concurrent/CancellationException"),"accelerator prefix cancelled");
}
extern "C" JNIEXPORT void JNICALL Java_com_google_ai_edge_examples_asr_NativePrefixRecognizer_begin0(JNIEnv* env,jclass,jlong id) {
  try {auto p=owner(id);std::lock_guard<std::mutex> lock(p->calls);p->check();p->recognizer->open();p->recognizer->begin();}
  catch(const std::exception& e){fail(env,e);}
}
extern "C" JNIEXPORT jbyteArray JNICALL Java_com_google_ai_edge_examples_asr_NativePrefixRecognizer_push0(JNIEnv* env,jclass,jlong id,jfloatArray pcm,jint offset,jint count) {
  try {
    if(!pcm || offset<0 || count<0 || count>32768 || offset>env->GetArrayLength(pcm)-count)
      throw std::invalid_argument("bounded PCM slice required");
    auto p=owner(id);std::lock_guard<std::mutex> lock(p->calls);p->check();
    std::vector<float> values(size_t(count),0);env->GetFloatArrayRegion(pcm,offset,count,values.data());java_error(env);
    return string_bytes(env,p->recognizer->push(values.data(),values.size()));
  } catch(const std::exception& e){fail(env,e);return nullptr;}
}
extern "C" JNIEXPORT jbyteArray JNICALL Java_com_google_ai_edge_examples_asr_NativePrefixRecognizer_finish0(JNIEnv* env,jclass,jlong id) {
  try {auto p=owner(id);std::lock_guard<std::mutex> lock(p->calls);p->check();return string_bytes(env,p->recognizer->finish());}
  catch(const std::exception& e){fail(env,e);return nullptr;}
}
extern "C" JNIEXPORT void JNICALL Java_com_google_ai_edge_examples_asr_NativePrefixRecognizer_reset0(JNIEnv* env,jclass,jlong id) {
  try {auto p=owner(id);std::lock_guard<std::mutex> lock(p->calls);p->check();p->recognizer->reset();}
  catch(const std::exception& e){fail(env,e);}
}
extern "C" JNIEXPORT void JNICALL Java_com_google_ai_edge_examples_asr_NativePrefixRecognizer_cancel0(JNIEnv* env,jclass,jlong id) {
  try {
    // Registry pin covers only the short cancel call, never inference/retire.
    std::lock_guard<std::mutex> lock(registry_mutex);auto found=registry.find(id);
    if(found==registry.end())throw std::runtime_error("native prefix owner retired");
    found->second->recognizer->cancel();
  }catch(const std::exception& e){fail(env,e);}
}
extern "C" JNIEXPORT void JNICALL Java_com_google_ai_edge_examples_asr_NativePrefixRecognizer_retire(JNIEnv* env,jclass,jlong id) {
  try {
    std::shared_ptr<Owner> p;
    {std::lock_guard<std::mutex> lock(registry_mutex);auto found=registry.find(id);
     if(found==registry.end())return;p=std::move(found->second);p->retired=true;registry.erase(found);}
    p->recognizer->cancel();std::lock_guard<std::mutex> lock(p->calls);
    p->recognizer->reset();p->recognizer.reset();
    if(p->lifecycle->load())throw std::runtime_error("accelerator close failed");
  } catch(const std::exception& e){fail(env,e);}
}
std::unique_ptr<aii::voice::Recognizer> aii::voice::make_java_prefix(JNIEnv* env,jobject decoder,
    std::vector<float> mel,std::shared_ptr<std::atomic<bool>> failed) {
  if(!failed)throw std::invalid_argument("retirement observation required");
  return std::make_unique<PrefixRecognizer>(std::make_unique<JavaDecoder>(env,decoder,std::move(mel),std::move(failed)),
                                          PrefixLimits{320,16000,320000});
}
