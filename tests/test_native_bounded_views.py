"""Compile the real descriptor helper; GPU parity is a separate native gate."""
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts.stage_native_single_prepare import bounded_step_views

ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'artifacts/native-pocket-source-20260910-r2/audio.cpp-3174e6b26f11a0e39b4f150961dce98f43ba860d'
HEADER=ROOT/'runtime/native_pocket/bounded_transfer_view.h'

PROBE=r'''
#include "bounded_transfer_view.h"
#include <cstring>
#include <iostream>
#include <vector>
bool fail_init=false;
extern "C" size_t ggml_nbytes(const ggml_tensor* t) {
    return size_t(t->ne[0]*t->ne[1]*t->ne[2]*t->ne[3])*sizeof(float);
}
extern "C" ggml_status ggml_backend_view_init(ggml_tensor* t) {
    if(fail_init)return GGML_STATUS_FAILED;
    t->buffer=t->view_src->buffer;
    t->data=static_cast<char*>(t->view_src->data)+t->view_offs;
    return GGML_STATUS_SUCCESS;
}
void require(bool ok,const char* message){if(!ok)throw std::runtime_error(message);}
template<class F> void refused(F f){bool caught=false;try{f();}catch(const std::runtime_error&){caught=true;}require(caught,"invalid descriptor accepted");}
int main(){try{
 for(size_t capacity:{1u,17u,1670u}) {
  const size_t width=8;
  std::vector<float> storage(capacity*width,-7.f);
  ggml_tensor root{},prototype{};
  root.type=prototype.type=GGML_TYPE_F32;
  for(int i=0;i<4;++i)root.ne[i]=prototype.ne[i]=1;
  root.ne[0]=static_cast<int64_t>(storage.size()); prototype.ne[0]=width;
  root.data=storage.data();root.buffer=reinterpret_cast<ggml_backend_buffer_t>(0x1000);
  prototype.view_src=&root;
  const auto original=prototype;
  for(size_t slot=0;slot<capacity;++slot) {
   const auto dst=native_cache_destination(prototype,slot,capacity);
   require(dst.data==storage.data()+slot*width,"wrong destination address");
   require(dst.view_offs==slot*width*sizeof(float),"wrong destination offset");
   require(dst.buffer==root.buffer,"wrong destination buffer");
   auto* data=static_cast<float*>(dst.data);
   for(size_t i=0;i<width;++i)data[i]=float(slot);
   if(slot+1<capacity)require(storage[(slot+1)*width]==-7.f,"next slot overwritten");
  }
  require(std::memcmp(&prototype,&original,sizeof(prototype))==0,"prototype mutated");
  for(size_t slot=0;slot<capacity;++slot)for(size_t i=0;i<width;++i)require(storage[slot*width+i]==float(slot),"prior slot corrupted");
  refused([&]{native_cache_destination(prototype,capacity,capacity);});
  refused([&]{native_cache_destination(prototype,0,capacity+1);});
  refused([&]{native_cache_destination(prototype,0,std::numeric_limits<size_t>::max());});
  fail_init=true;refused([&]{native_cache_destination(prototype,0,capacity);});fail_init=false;
  prototype.view_offs=4;refused([&]{native_cache_destination(prototype,0,capacity);});prototype.view_offs=0;
  prototype.type=GGML_TYPE_F16;refused([&]{native_cache_destination(prototype,0,capacity);});
 }
 std::cout<<"all slot, extent, ownership and initialization cases passed\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
'''


@pytest.mark.parametrize('mutation',[None,'offset','bound','failed_init'])
def test_real_header_and_compiling_falsifiers(tmp_path,mutation):
    header=HEADER.read_text()
    if mutation=='offset': header=header.replace('destination.view_offs = slot * bytes;','destination.view_offs = 0;')
    elif mutation=='bound': header=header.replace('slot >= capacity','false')
    elif mutation=='failed_init': header=header.replace('ggml_backend_view_init(&destination) != GGML_STATUS_SUCCESS','ggml_backend_view_init(&destination) != GGML_STATUS_SUCCESS && false')
    (tmp_path/'bounded_transfer_view.h').write_text(header)
    (tmp_path/'probe.cpp').write_text(PROBE)
    compiler=shutil.which('clang++')
    assert compiler, 'native descriptor falsifier compiler is required'
    build=subprocess.run([compiler,'-std=c++17','-Wall','-Wextra','-Werror','-I',str(ENGINE/'external/ggml/include'),str(tmp_path/'probe.cpp'),'-o',str(tmp_path/'probe')],capture_output=True,text=True)
    assert build.returncode==0,build.stderr  # A compile failure is NOT a kill.
    run=subprocess.run([str(tmp_path/'probe')],capture_output=True,text=True)
    if mutation is None: assert run.returncode==0,run.stderr
    else:
        assert run.returncode==1
        assert ('wrong destination address' if mutation=='offset' else 'invalid descriptor accepted') in run.stderr


def test_only_vulkan_step_destinations_change_not_prompt_or_arithmetic():
    original=(ENGINE/'src/models/pocket_tts/flow_lm.cpp').read_bytes()
    changed=bounded_step_views(original)
    assert changed.count(b'core::BackendType::Vulkan')==2
    assert b'? 1 : cache_steps_' in changed
    assert original.split(b'    void apply_prompt_from_state(')[1].split(b'    FlowLMStepResult run_in_place')[0]==changed.split(b'    void apply_prompt_from_state(')[1].split(b'    FlowLMStepResult run_in_place')[0]
    assert original.split(b'        if (prompt_steps_ > 0) {\n            prompt_step_key_sources_')[1]==changed.split(b'        if (prompt_steps_ > 0) {\n            prompt_step_key_sources_')[1]
    with pytest.raises(ValueError): bounded_step_views(original+b'changed')
