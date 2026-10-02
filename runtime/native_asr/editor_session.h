#pragma once
#include "initializers.h"
#include "../native/platform/startup_trace.h"
#include <cstring>
#include <set>
#include <stdexcept>
#ifdef __APPLE__
#include <mach/mach.h>
#include <os/proc.h>
#include <cstdio>
#endif

namespace aii::asr {
inline void editor_memory(const char* phase) {
#ifdef __APPLE__
  task_vm_info_data_t info{};
  mach_msg_type_number_t count=TASK_VM_INFO_COUNT;
  if(task_info(mach_task_self(),TASK_VM_INFO,reinterpret_cast<task_info_t>(&info),&count)==KERN_SUCCESS)
    std::fprintf(stderr,"{\"kind\":\"editor_memory\",\"phase\":\"%s\",\"physical_footprint\":%llu,\"resident\":%llu,\"available\":%zu}\n",
        phase,static_cast<unsigned long long>(info.phys_footprint),
        static_cast<unsigned long long>(info.resident_size),os_proc_available_memory());
#else
  (void)phase;
#endif
}
// Private candidate only. The owner must retain Initializers (and its verified
// read-only mappings) until AFTER the returned session has been destroyed.
// No ONNX path is opened by this code: the valid base has typed weight inputs,
// not external-file references. ModelEditor binds exactly those named values.
inline Ort::Session editor_session(Ort::Env& env, Initializers& init,
                                   const Ort::SessionOptions& options) {
  editor_memory("before_editor_create");
  auto session=Ort::Session::CreateModelEditorSession(
      env,init.encoder.data(),init.encoder.size(),options);
  editor_memory("after_editor_create");
  Ort::AllocatorWithDefaultOptions allocator;
  const std::vector<std::string> expected{
      "audio_signal", "length", "cache_last_channel", "cache_last_time",
      "cache_last_channel_len", "prompt_index", "diagnostic_drop"};
  // Use the base's first seven real inputs, retaining their exact type info.
  // Full signature/geometry is also checked by the ordinary ASR owner.
  if (session.GetInputCount()!=7+init.names.size())
    throw std::runtime_error("editor input census differs");
  Ort::Graph graph;
  std::vector<Ort::ValueInfo> inputs;
  std::set<std::string> weights(init.names.begin(),init.names.end());
  for(size_t i=0;i<session.GetInputCount();++i) {
    const std::string name=session.GetInputNameAllocated(i,allocator).get();
    if(i<7) {
      if(name!=expected[i] || weights.count(name))
        throw std::runtime_error("editor real input differs");
      auto type=session.GetInputTypeInfo(i);
      inputs.emplace_back(name,type.GetConst());
    } else if(!weights.erase(name))throw std::runtime_error("editor weight identity differs");
  }
  if(!weights.empty())throw std::runtime_error("editor weight missing");
  graph.SetInputs(inputs);
  for(size_t i=0;i<init.names.size();++i) {
    auto& value=init.values[i];
    auto type=value.GetTensorTypeAndShapeInfo();
    const auto bytes=type.GetElementCount()*sizeof(float);
    if(bytes>=128) {
      graph.AddInitializer(init.names[i],value,true);
    } else {
      // ORT requires small shape-related constants to be owned. Copy only
      // these bounded scalars, never a checkpoint-sized buffer.
      auto shape=type.GetShape();
      auto owned=Ort::Value::CreateTensor<float>(allocator,shape.data(),shape.size());
      std::memcpy(owned.GetTensorMutableData<float>(),value.GetTensorData<float>(),bytes);
      graph.AddInitializer(init.names[i],owned,false);
    }
  }
  Ort::Model update({{"",session.GetOpset("")}});
  update.AddGraph(graph);
  {
    aii::platform::StartupSpan span("editor_finalize","native-asr-startup-profile");
    editor_memory("before_finalize");
    try {session.FinalizeModelEditorSession(update,options,nullptr);}
    catch(...) {editor_memory("failed_finalize");throw;}
    editor_memory("after_finalize");
  }
  if(session.GetInputCount()!=7)throw std::runtime_error("editor callable signature differs");
  init.check_unchanged();
  return session;
}
}  // namespace aii::asr
