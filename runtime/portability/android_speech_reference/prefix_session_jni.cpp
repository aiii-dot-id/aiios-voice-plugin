#include "prefix_jni.h"
#include "c_api_internal.h"
#include <cstdio>
#include <fcntl.h>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <unistd.h>

// The real, unchanged C ABI fixture is linked with only its loader symbol
// renamed. The replacement runs the same factory and every same session call.
int aii_pixel_fixture_main(int,char**);
namespace {
thread_local std::unique_ptr<aii::voice::Recognizer> pending_recognizer;
std::mutex probe_owner;
std::string utf8(JNIEnv* env,jstring value){
  if(!value)throw std::invalid_argument("fixture string required");
  const char* raw=env->GetStringUTFChars(value,nullptr);
  if(!raw)throw std::runtime_error("fixture string unavailable");
  std::string text(raw);env->ReleaseStringUTFChars(value,raw);
  for(unsigned char c:text)if(c>=128 || c<32)throw std::invalid_argument("ASCII fixture paths required");
  return text;
}
struct Capture {
  int stdout_copy=-1,stderr_copy=-1;
  explicit Capture(const std::string& root){
    std::cout.flush();std::cerr.flush();std::fflush(nullptr);
    stdout_copy=dup(STDOUT_FILENO);stderr_copy=dup(STDERR_FILENO);
    // App-owned external diagnostic directory: match Java evidence permissions
    // so adb can retrieve sealed logs. No microphone or production records.
    int out=open((root+"/session.stdout").c_str(),O_WRONLY|O_CREAT|O_EXCL,0660);
    int err=open((root+"/session.stderr").c_str(),O_WRONLY|O_CREAT|O_EXCL,0660);
    const bool ready=stdout_copy>=0 && stderr_copy>=0 && out>=0 && err>=0;
    const bool redirected=ready && dup2(out,STDOUT_FILENO)>=0 && dup2(err,STDERR_FILENO)>=0;
    if(out>=0)close(out);if(err>=0)close(err);
    if(!redirected){restore();throw std::runtime_error("fresh native evidence capture unavailable");}
  }
  void restore()noexcept{
    std::cout.flush();std::cerr.flush();std::fflush(nullptr);
    if(stdout_copy>=0){dup2(stdout_copy,STDOUT_FILENO);close(stdout_copy);stdout_copy=-1;}
    if(stderr_copy>=0){dup2(stderr_copy,STDERR_FILENO);close(stderr_copy);stderr_copy=-1;}
  }
  ~Capture(){restore();}
};
}
extern "C" aii_voice_result aii_pixel_load_with_uid(const aii_voice_paths* paths,const char* backend,
  const char* uid,const char* policy,size_t bytes,aii_voice_snapshot_reader read,void* context,
  aii_voice_models** result,aii_voice_error* error){
  if(!pending_recognizer){std::snprintf(error->message,sizeof error->message,"GPU recognizer missing; no CPU fallback");return AII_VOICE_FAILED;}
  return aii::voice::load_native_models(paths,backend,uid,policy,bytes,read,context,result,error,std::move(pending_recognizer));
}
extern "C" JNIEXPORT jint JNICALL Java_com_google_ai_edge_examples_asr_NativeVoiceSession_run(
    JNIEnv* env,jclass,jobject decoder,jfloatArray mel,jobjectArray arguments,jstring logroot){
  try {
    std::unique_lock<std::mutex> owner(probe_owner,std::try_to_lock);
    if(!owner.owns_lock())throw std::runtime_error("recorded session already running");
    if(!mel || env->GetArrayLength(mel)!=128*257 || !arguments || env->GetArrayLength(arguments)!=13)
      throw std::invalid_argument("exact coefficients and five-model fixture arguments required");
    std::vector<std::string> strings{"aii-c-api-pixel-gpu"};
    for(int i=0;i<13;++i){auto value=static_cast<jstring>(env->GetObjectArrayElement(arguments,i));
      strings.push_back(utf8(env,value));env->DeleteLocalRef(value);}
    if(strings[10]!="vulkan")throw std::invalid_argument("Vulkan TTS required; no CPU fallback");
    std::vector<char*> argv;for(auto& s:strings)argv.push_back(s.data());
    std::vector<float> coefficients(128*257);env->GetFloatArrayRegion(mel,0,coefficients.size(),coefficients.data());
    if(env->ExceptionCheck())return -1;
    auto failed=std::make_shared<std::atomic<bool>>(false);
    Capture capture(utf8(env,logroot));
    setenv("ORT_DISABLE_TELEMETRY","1",1);setenv("GGML_VK_DISABLE_F16","1",1);
    setenv("GGML_VK_VISIBLE_DEVICES","0",1);setenv("AII_VK_F32_TILED","1",1);
    pending_recognizer=aii::voice::make_java_prefix(env,decoder,std::move(coefficients),failed);
    const int result=aii_pixel_fixture_main(int(argv.size()),argv.data());
    pending_recognizer.reset();
    if(failed->load())throw std::runtime_error("GPU recognizer teardown failed");
    return result;
  }catch(const std::exception& error){
    pending_recognizer.reset();
    if(!env->ExceptionCheck())env->ThrowNew(env->FindClass("java/lang/IllegalStateException"),error.what());
    return -1;
  }
}
