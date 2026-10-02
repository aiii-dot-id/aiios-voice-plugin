// Offline composition qualification. Explicit output directory contains speech
// and identity evidence; keep it private. Does not open an identity or device.
#include "recognizer.h"
#ifdef AII_COREML_SEPARATOR
#include "coreml_separator.h"
#else
#include "onnx_separator.h"
#endif
#include <chrono>
#include <charconv>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <thread>

namespace {
void check(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
std::vector<char> bytes(const char* path,size_t limit) {
  std::ifstream f(std::filesystem::u8path(path),std::ios::binary|std::ios::ate);const auto n=f.tellg();
  check(f&&n>0&&uint64_t(n)<=limit,"input file extent");
  std::vector<char> result(static_cast<size_t>(n));f.seekg(0);
  check(bool(f.read(result.data(),n)),"input file read");return result;
}
std::vector<float> floats(const char* path,size_t limit) {
  const auto raw=bytes(path,limit*4);check(raw.size()%4==0,"float extent");
  std::vector<float> result(raw.size()/4);std::memcpy(result.data(),raw.data(),raw.size());return result;
}
std::vector<std::string> vocabulary(const char* path) {
  const auto raw=bytes(path,1024*1028);size_t offset=0;std::vector<std::string> words;
  for(size_t i=0;i<1024;++i) {
    check(raw.size()-offset>=4,"token length read");
    const auto* p=reinterpret_cast<const unsigned char*>(raw.data()+offset);
    const uint32_t n=uint32_t(p[0])|(uint32_t(p[1])<<8)|(uint32_t(p[2])<<16)|(uint32_t(p[3])<<24);
    offset+=4;check(n<=1024&&n<=raw.size()-offset,"token length bound");
    words.emplace_back(raw.data()+offset,n);offset+=n;
  }
  check(offset==raw.size(),"trailing token bytes");return words;
}
void text(std::ostream& out,const std::string& value) {
  constexpr char hex[]="0123456789abcdef";out<<'"';
  for(unsigned char c:value) {
    if(c=='"'||c=='\\')out<<'\\'<<char(c);
    else if(c<32)out<<"\\u00"<<hex[c>>4]<<hex[c&15];
    else out<<char(c);
  }
  out<<'"';
}
void save(const std::filesystem::path& file,const std::vector<float>& pcm) {
  check(!std::filesystem::exists(file),"output already exists");
  std::ofstream f(file,std::ios::binary);f.write(reinterpret_cast<const char*>(pcm.data()),
    static_cast<std::streamsize>(pcm.size()*sizeof(float)));f.close();check(bool(f),"output file write");
}
double seconds(std::chrono::steady_clock::time_point since) {
  return std::chrono::duration<double>(std::chrono::steady_clock::now()-since).count();
}
template<class Work,class Stop>
void cancel_trial(const char* label,unsigned delay,Work work,Stop stop) {
  std::chrono::steady_clock::time_point requested,retired;
  std::thread interrupt([&]{std::this_thread::sleep_for(std::chrono::milliseconds(delay));
    requested=std::chrono::steady_clock::now();stop();});
  bool cancelled=false;std::exception_ptr failure;
  try{work();}catch(const aii::voice::Cancelled&){cancelled=true;}catch(...){failure=std::current_exception();}
  retired=std::chrono::steady_clock::now();interrupt.join();
  if(failure)std::rethrow_exception(failure);
  check(cancelled,"requested interruption did not cancel active inference");
  const auto ms=std::chrono::duration<double,std::milli>(retired-requested).count();
  check(ms>=0&&ms<200,"native model cancellation retirement exceeded 200 ms");
  std::cout<<"{\"kind\":\"cancelled\",\"component\":\""<<label<<"\",\"requested_after_ms\":"<<delay
    <<",\"retired_after_ms\":"<<ms<<"}"<<std::endl;
}
}
int main(int argc,char** argv){try{
  check(argc>=11,"graphs mel framed-tokens Nemotron GPU separator threads CUDA output-dir recordings required");
  int first=10;
  aii::multitalker::EncoderExecution execution;
  bool refine_evidence=false;
  if(std::string(argv[first])=="--encoder-threads") {
    check(argc>=13,"encoder threads and recordings required");
    const auto end=argv[first+1]+std::strlen(argv[first+1]);
    const auto parsed=std::from_chars(argv[first+1],end,execution.threads);
    check(parsed.ec==std::errc{}&&parsed.ptr==end&&execution.threads>=1&&execution.threads<=16,
          "encoder threads must be an integer from 1 through 16");
    first+=2;
  }
  if(first<argc&&std::string(argv[first])=="--refine-evidence") {
    refine_evidence=true;++first;
  }
  check(first<argc,"recordings required");
  const auto output=std::filesystem::u8path(argv[9]);
  check(std::filesystem::create_directory(output),"output directory must be new");
  const auto mel=floats(argv[2],128*257);
  const auto loaded=std::chrono::steady_clock::now();
#ifdef AII_COREML_SEPARATOR
  check(std::string(argv[7])=="0"&&std::string(argv[8])=="-1","Core ML has no ORT thread/CUDA options");
  aii::multitalker::CoreMLSeparator separator(argv[6]);
#else
  const auto model=bytes(argv[6],512u*1024*1024);
  aii::multitalker::OnnxSeparator separator(model.data(),model.size(),std::stoi(argv[7]),std::stoi(argv[8]));
#endif
  aii::multitalker::Recognizer recognizer(argv[1],mel.data(),mel.size(),vocabulary(argv[3]),
      {argv[4],std::stoi(argv[5]),true,refine_evidence},execution);
  std::atomic<bool> cancelled{false};
  std::cout<<"{\"kind\":\"loaded\",\"seconds\":"<<seconds(loaded)
    <<",\"encoder_threads\":"<<execution.threads
    <<",\"refine_evidence\":"<<(refine_evidence?"true":"false")<<"}"<<std::endl;
  const auto initial=floats(argv[first],480000);
#ifdef AII_COREML_SEPARATOR
  // Readiness/warmup is explicit and outside the measured interruption gate.
  separator.separate(initial);
  constexpr unsigned separator_cancel_ms=25;
#else
  constexpr unsigned separator_cancel_ms=1000;
#endif
  cancel_trial("separator",separator_cancel_ms,[&]{separator.separate(initial);},[&]{separator.cancel();});
  separator.open();const auto recovery=separator.separate(initial);
  cancel_trial("source_recognition",250,
    [&]{aii::multitalker::bind_source_text(recognizer,recovery,1,cancelled);},
    [&]{cancelled.store(true);recognizer.cancel();});
  cancelled.store(false);
  for(int item=first;item<argc;++item) {
    const auto pcm=floats(argv[item],480000);const auto capture=static_cast<uint64_t>(item-first+1);
    const auto start=std::chrono::steady_clock::now();separator.open();const auto sources=separator.separate(pcm);
    const auto separation_seconds=seconds(start);const auto speech_start=std::chrono::steady_clock::now();
    const auto rows=aii::multitalker::bind_source_text(recognizer,sources,capture,cancelled);
    const auto recognition_seconds=seconds(speech_start);
    const auto prefix=std::to_string(capture);
    for(size_t i=0;i<2;++i)save(output/(prefix+"-source-"+std::to_string(i)+".f32"),sources[i]);
    std::cout<<"{\"kind\":\"capture\",\"capture\":"<<capture<<",\"input_samples\":"<<pcm.size()
      <<",\"separation_seconds\":"<<separation_seconds<<",\"recognition_seconds\":"<<recognition_seconds<<",\"segments\":[";
    for(size_t i=0;i<rows.size();++i) {
      const auto& row=rows[i];if(i)std::cout<<',';
      if(!row.evidence.empty())save(output/(prefix+"-evidence-"+std::to_string(i)+".f32"),row.evidence);
      std::cout<<"{\"track\":";text(std::cout,row.track);std::cout<<",\"text\":";text(std::cout,row.text);
      std::cout<<",\"start\":"<<row.start<<",\"end\":"<<row.end<<",\"evidence_start\":"<<row.evidence_start
        <<",\"evidence_samples\":"<<row.evidence.size()<<",\"evidence_unavailable\":";text(std::cout,row.evidence_unavailable);
      std::cout<<",\"evidence_regions\":[";
      for(size_t j=0;j<row.evidence_regions.size();++j){if(j)std::cout<<',';std::cout<<'['<<row.evidence_regions[j].first<<','<<row.evidence_regions[j].second<<']';}
      std::cout<<"]}";
    }
    std::cout<<"]}"<<std::endl;
  }
  return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
