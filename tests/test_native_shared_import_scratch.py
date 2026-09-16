"""Compile the actual cache against real ggml storage; no model or live install."""
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.stage_native_single_prepare import shared_import_scratch
from scripts.audit_native_single_prepare import verify_shared_scratch_source

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT/'artifacts/native-pocket-source-20260910-r2/audio.cpp-3174e6b26f11a0e39b4f150961dce98f43ba860d'
BUILD = ROOT/'.build/native-pocket-macos-20260910-r5/build'
ORIGINAL = ENGINE/'src/framework/runtime/kv_cache.cpp'
PROBE = r'''
#include "engine/framework/core/module.h"
#include "engine/framework/core/backend.h"
#include <cstdint>
#include <string>
#include <vector>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <algorithm>
// Test-only access: assert the actual allocation count, not an API counter.
#define private public
#include "engine/framework/runtime/kv_cache.h"
#undef private
#include "ggml-cpu.h"
#include "ggml-alloc.h"
using namespace engine;
void need(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
int main(){try{
 for(int layers:{1,3,6}) for(int capacity:{1,7,257}) {
  const int width=64;
  auto* ctx=ggml_init({1024*1024,nullptr,true});need(ctx,"context");
  core::ModuleBuildContext mc{ctx,"shared-scratch-proof",core::BackendType::Cpu};
  std::vector<core::TensorValue> keys,values;
  for(int l=0;l<layers;++l){
   keys.push_back(core::make_tensor(mc,GGML_TYPE_F32,core::TensorShape::from_dims({1,capacity,width})));
   values.push_back(core::make_tensor(mc,GGML_TYPE_F32,core::TensorShape::from_dims({1,capacity,width})));
  }
  runtime::TransformerKVCache cache(capacity,width,keys,values);
  size_t scratch=0;
  for(const auto& layer:cache.layers_)scratch+=layer.import_key_scratch.capacity()+layer.import_value_scratch.capacity();
  need(scratch==size_t(2*capacity*width),"per-layer scratch allocation returned");
  auto* backend=ggml_backend_cpu_init();auto* buffer=ggml_backend_alloc_ctx_tensors(ctx,backend);
  need(buffer,"allocated tensor storage");
  for(int valid:{capacity,1,0,capacity/2,capacity,0}) {
   runtime::TransformerKVState state;state.current_end=valid;
   state.layers.resize(layers);
   for(int l=0;l<layers;++l){
    auto& row=state.layers[l];row.valid_steps=valid;
    row.key.resize(valid*width);row.value.resize(valid*width);
    for(size_t i=0;i<row.key.size();++i){row.key[i]=float(l*10000+i)*.25f;row.value[i]=float(l*7000+i)*-.5f;}
    if(valid){row.key[0]=-0.f;row.value[0]=-float(l+1);}
    std::vector<float> dirty(capacity*width,-313.f);
    core::write_tensor_f32(keys[l],dirty);core::write_tensor_f32(values[l],dirty);
   }
   cache.import_state(state);
   need(cache.valid_steps()==valid&&cache.current_end()==valid,"validity changed");
   for(int l=0;l<layers;++l)for(int kind=0;kind<2;++kind){
    const auto& input=kind?state.layers[l].value:state.layers[l].key;
    auto actual=core::read_tensor_f32((kind?values[l]:keys[l]).tensor);
    std::vector<float> expected(capacity*width,0.f);
    std::copy(input.begin(),input.end(),expected.begin());
    need(actual.size()==expected.size()&&std::memcmp(actual.data(),expected.data(),expected.size()*4)==0,
         "cross-layer prefix, signed zero or dirty tail differs");
   }
   auto exported=cache.export_state();need(exported.layers.size()==size_t(layers),"export layers");
   for(int l=0;l<layers;++l){need(exported.layers[l].key==state.layers[l].key,"export keys");need(exported.layers[l].value==state.layers[l].value,"export values");}
  }
  ggml_backend_buffer_free(buffer);ggml_free(ctx);ggml_backend_free(backend);
 }
 runtime::TransformerKVCache empty(0,64,{},{});empty.import_state({});need(empty.export_state().layers.empty(),"empty cache");
 std::cout<<"actual cache: 1/3/6 layers, full/short/empty repeated imports, independent keys/values, signed zero, dirty tails and one scratch pair passed\n";
 return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
'''


@pytest.mark.parametrize('mutation',[None,'per-layer','key-value-alias','stale-tail'])
def test_real_cache_and_compiling_mutations(tmp_path,mutation):
    body=shared_import_scratch(ORIGINAL.read_bytes())
    if mutation=='per-layer':
        body=body.replace(b'layer == 0 ? cache_elems : 0',b'cache_elems')
    elif mutation=='key-value-alias':
        body=body.replace(b'scratch.import_value_scratch',b'scratch.import_key_scratch')
    elif mutation=='stale-tail':
        body=body.replace(b'std::fill(scratch.import_key_scratch.begin(), scratch.import_key_scratch.end(), 0.0F);',b'(void)scratch;')
    (tmp_path/'kv.cpp').write_bytes(body);(tmp_path/'probe.cpp').write_text(PROBE)
    libraries=[BUILD/'libengine_runtime.a',*(BUILD/'ggml/src'/n for n in ('libggml.a','libggml-cpu.a','ggml-blas/libggml-blas.a','libggml-base.a'))]
    build=subprocess.run([shutil.which('clang++'),'-std=c++17','-O2','-I',str(ENGINE/'include'),'-I',str(ENGINE/'external/ggml/include'),
                          str(tmp_path/'probe.cpp'),str(tmp_path/'kv.cpp'),*map(str,libraries),'-framework','Accelerate','-o',str(tmp_path/'probe')],capture_output=True,text=True,timeout=60)
    assert build.returncode==0,build.stderr
    run=subprocess.run([str(tmp_path/'probe')],capture_output=True,text=True,timeout=30)
    if mutation is None:assert run.returncode==0,run.stderr
    else:
        assert run.returncode==1,run.stderr
        assert ('per-layer scratch allocation returned' if mutation=='per-layer' else 'cross-layer prefix, signed zero or dirty tail differs') in run.stderr


@pytest.mark.parametrize('damage',[None,'validity','write','batched','allocation'])
def test_only_nonbatched_scratch_ownership_changes(damage):
    old=ORIGINAL.read_bytes();new=shared_import_scratch(old)
    verify_shared_scratch_source(old,new)
    if damage=='validity':new=new.replace(b'valid_steps_ = state_steps;',b'valid_steps_ = state_steps+1;',1)
    elif damage=='write':new=new.replace(b'write_cache_tensor(cache.key_tensor, scratch.import_key_scratch, options_);',b';')
    elif damage=='batched':new=new.replace(b'batch_size_(std::max<int64_t>(0, batch_size))',b'batch_size_(2)')
    elif damage=='allocation':new=new.replace(b'layer == 0 ? cache_elems : 0',b'cache_elems')
    else:return
    assert new!=shared_import_scratch(old),'mutation did not change source'
    with pytest.raises(AssertionError):verify_shared_scratch_source(old,new)


def test_wrong_parent_refused():
    with pytest.raises(ValueError):shared_import_scratch(ORIGINAL.read_bytes()+b'changed')
