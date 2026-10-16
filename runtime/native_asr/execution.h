#pragma once
#include "execution_policy.h"
#include "onnxruntime_cxx_api.h"
#ifdef AII_ASR_DIRECTML
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <dxgi1_6.h>
#include <d3d12.h>
#include <wrl/client.h>
#include "dml_provider_factory.h"
#endif

namespace aii::asr {
inline std::string configure_encoder(Ort::SessionOptions& options,const ExecutionPolicy& policy) {
  using namespace aii::voice::wire;
  auto report=object();
  put(report,"hardware_execution_verified",boolean(false));
  put(report,"evidence_scope",string("session_configuration; kernel placement needs profiling"));
  if(!policy.directml) {
#ifdef AII_MOBILE_COREML_CANDIDATE
    if(policy.target_default) {
    put(report,"encoder_provider",string("CoreMLExecutionProvider"));
    put(report,"decoder_provider",string("CoreMLExecutionProvider"));
    put(report,"joiner_provider",string("CoreMLExecutionProvider"));
    put(report,"compute_units_requested",string("CPUAndNeuralEngine"));
    put(report,"cpu_partitions_allowed",boolean(true));
    return encode(report);
    }
#endif
    put(report,"encoder_provider",string("CPUExecutionProvider"));
    put(report,"decoder_provider",string("CPUExecutionProvider"));
    put(report,"joiner_provider",string("CPUExecutionProvider"));
    put(report,"cpu_partitions_allowed",boolean(false));
    (void)options;
    return encode(report);
  }
#if defined(AII_ASR_DIRECTML) && !defined(AII_MOBILE_COREML_CANDIDATE)
  const auto providers=Ort::GetAvailableProviders();
  require(std::find(providers.begin(),providers.end(),"DmlExecutionProvider")!=providers.end(),
          "DirectML unavailable; no CPU substitution");
  using Microsoft::WRL::ComPtr;
  ComPtr<IDXGIFactory6> factory;
  require(SUCCEEDED(CreateDXGIFactory1(IID_PPV_ARGS(&factory))),"DXGI GPU preference discovery unavailable");
  std::vector<ComPtr<IDXGIAdapter1>> adapters;
  std::vector<DXGI_ADAPTER_DESC1> descriptions;
  std::vector<AdapterCandidate> candidates;
  for(UINT i=0;;++i) {
    ComPtr<IDXGIAdapter1> adapter;
    const auto hr=factory->EnumAdapters1(i,&adapter);
    if(hr==DXGI_ERROR_NOT_FOUND)break;
    require(SUCCEEDED(hr)&&i<128,"DXGI enumeration failed or exceeds bound");
    DXGI_ADAPTER_DESC1 desc{};
    require(SUCCEEDED(adapter->GetDesc1(&desc)),"DXGI adapter description unavailable");
    const bool software=(desc.Flags&DXGI_ADAPTER_FLAG_SOFTWARE)!=0;
    adapters.push_back(adapter);descriptions.push_back(desc);
    candidates.push_back({i,128,software,false});
  }
  for(UINT rank=0;rank<adapters.size();++rank) {
    ComPtr<IDXGIAdapter1> adapter;
    require(SUCCEEDED(factory->EnumAdapterByGpuPreference(rank,DXGI_GPU_PREFERENCE_HIGH_PERFORMANCE,
            IID_PPV_ARGS(&adapter))),"DXGI preference enumeration changed");
    DXGI_ADAPTER_DESC1 desc{};
    require(SUCCEEDED(adapter->GetDesc1(&desc)),"DXGI preferred description unavailable");
    auto it=std::find_if(descriptions.begin(),descriptions.end(),[&](const auto& d){
      return d.AdapterLuid.HighPart==desc.AdapterLuid.HighPart&&d.AdapterLuid.LowPart==desc.AdapterLuid.LowPart;});
    require(it!=descriptions.end(),"DXGI adapter census changed during selection");
    auto& candidate=candidates[size_t(it-descriptions.begin())];
    require(candidate.preference==128,"duplicate DXGI preference identity");candidate.preference=rank;
  }
  // Probe in preference order and stop at the first usable requested device.
  // Querying every hardware interface can initialize unused driver paths.
  std::vector<unsigned> order;
  for(unsigned i=0;i<candidates.size();++i)order.push_back(i);
  std::stable_sort(order.begin(),order.end(),[&](unsigned a,unsigned b){return candidates[a].preference<candidates[b].preference;});
  for(auto i:order) {
    auto& candidate=candidates[i];
    if(candidate.software||(policy.adapter>=0&&candidate.index!=unsigned(policy.adapter)))continue;
    // S_FALSE is success for a null-output support query; no disposable device
    // is created here. ORT constructs the one device that will actually run.
    candidate.d3d12=SUCCEEDED(D3D12CreateDevice(adapters[i].Get(),D3D_FEATURE_LEVEL_11_0,__uuidof(ID3D12Device),nullptr));
    if(candidate.d3d12)break;
  }
  const auto index=select_adapter(policy,candidates);const auto& desc=descriptions[index];
  const OrtDmlApi* api=nullptr;
  Ort::ThrowOnError(Ort::GetApi().GetExecutionProviderApi("DML",ORT_API_VERSION,reinterpret_cast<const void**>(&api)));
  require(api!=nullptr,"DirectML factory unavailable");
  options.SetExecutionMode(ORT_SEQUENTIAL);options.DisableMemPattern();
  Ort::ThrowOnError(api->SessionOptionsAppendExecutionProvider_DML(options,int(index)));
  char description[1024]{};
  require(WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,desc.Description,-1,description,sizeof description,nullptr,nullptr)>0,
          "DXGI description UTF-8 conversion failed");
  put(report,"encoder_provider",string("DmlExecutionProvider"));
  put(report,"decoder_provider",string("CPUExecutionProvider"));
  put(report,"joiner_provider",string("CPUExecutionProvider"));
  put(report,"cpu_partitions_allowed",boolean(true));
  auto device=object();put(device,"index",number(index));
  put(device,"selection",policy.adapter<0?string("high_performance"):string("explicit_index"));
  put(device,"name",string(description));put(device,"vendor_id",number(desc.VendorId));
  put(device,"device_id",number(desc.DeviceId));put(device,"dedicated_bytes",number(desc.DedicatedVideoMemory));
  put(device,"luid",string(std::to_string(desc.AdapterLuid.HighPart)+":"+std::to_string(desc.AdapterLuid.LowPart)));
  put(report,"adapter",std::move(device));
  return encode(report);
#else
  throw std::runtime_error("DirectML was not built for this target; no CPU substitution");
#endif
}
}
