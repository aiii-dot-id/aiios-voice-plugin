"""Real ggml allocation/compute, retained post-graph reads, failure rollback."""
from pathlib import Path
import shutil
import subprocess

import pytest
from scripts.stage_native_single_prepare import persistent_graph_storage

ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'artifacts/native-pocket-source-20260910-r2/audio.cpp-3174e6b26f11a0e39b4f150961dce98f43ba860d'
BUILD=ROOT/'.build/ggml-storage-proof-20260913-r1'
HEADER=ROOT/'runtime/native_pocket/persistent_graph_storage.h'
PROBE=r'''
#include "ggml.h"
#include "ggml-backend.h"
#include "ggml-cpu.h"
#include "ggml-alloc.h"
#include <iostream>
#include <vector>
#include <stdexcept>
static int bind_count=0,fail_binding=0;
static bool fail_view=false;
ggml_status checked_alloc(ggml_backend_buffer_t b,ggml_tensor* t,void* p) {
 const auto code=ggml_backend_tensor_alloc(b,t,p);
 if(++bind_count==fail_binding)return GGML_STATUS_FAILED;
 return code;
}
ggml_status checked_view(ggml_tensor* t) {
 const auto code=ggml_backend_view_init(t);
 return fail_view?GGML_STATUS_FAILED:code;
}
#define ggml_backend_tensor_alloc checked_alloc
#define ggml_backend_view_init checked_view
#include "persistent_graph_storage.h"
#undef ggml_backend_tensor_alloc
#undef ggml_backend_view_init
void require(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
int main(){try{
 auto* backend=ggml_backend_cpu_init();require(backend,"backend");
 ggml_backend_cpu_set_n_threads(backend,1);
 for(int fail:{0,2}) {
  auto* c=ggml_init({16*1024*1024,nullptr,true});require(c,"context");
  auto* x=ggml_new_tensor_1d(c,GGML_TYPE_F32,1024);
  auto* y=ggml_new_tensor_1d(c,GGML_TYPE_F32,1024);
  auto* cache=ggml_new_tensor_1d(c,GGML_TYPE_F32,4096);
  auto* dest=ggml_view_1d(c,cache,1024,1024*sizeof(float));
  auto* key=ggml_add(c,x,y);
  auto* value=ggml_mul(c,key,y);
  auto* key_source=ggml_view_1d(c,key,1024,0);
  auto* result=value;
  for(int i=0;i<20;++i) result=ggml_add(c,result,y);
  native_retain_graph_value(key_source);
  native_retain_graph_value(value);
  native_retain_graph_value(result);
  bind_count=0;fail_binding=fail;
  auto* persistent=native_allocate_persistent_leaves(c,backend);
  if(fail){
   require(!persistent,"binding failure reported success");
   require(!x->buffer&&!x->data&&!y->buffer&&!y->data&&!cache->buffer&&!cache->data,"partial binding survived failure");
   fail_binding=0;bind_count=0;
   persistent=native_allocate_persistent_leaves(c,backend);
  }
  require(persistent&&x->data&&y->data&&cache->data,"persistent leaves absent");
  require(!key->data&&!value->data&&!result->data,"graph intermediates allocated permanently");
  require(ggml_backend_buffer_get_size(persistent)<30*1024,"persistent footprint contains temporaries");
  fail_view=true;
  require(!native_bind_ready_view(dest),"view failure reported success");
  require(!dest->data&&!dest->buffer,"failed view remained bound");
  fail_view=false;
  require(native_bind_persistent_views(c)&&dest->data,"persistent view missing");
  require(!key_source->data,"off-graph source initialized before allocation");
  auto* g=ggml_new_graph_custom(c,256,false);ggml_build_forward_expand(g,result);
  auto* allocator=ggml_gallocr_new(ggml_backend_get_default_buffer_type(backend));
  require(ggml_gallocr_alloc_graph(allocator,g),"graph allocation failed");
  require(native_bind_ready_view(key_source),"post-compute source view missing");
  std::vector<float>a(1024),b(1024),out(1024),key_out(1024),v_out(1024),state(4096,-7.f);
  for(int pass=0;pass<3;++pass){
   for(size_t i=0;i<a.size();++i){a[i]=float(i+pass);b[i]=2.f;}
   ggml_backend_tensor_set(x,a.data(),0,a.size()*sizeof(float));
   ggml_backend_tensor_set(y,b.data(),0,b.size()*sizeof(float));
   ggml_backend_tensor_set(cache,state.data(),0,state.size()*sizeof(float));
   require(ggml_backend_graph_compute(backend,g)==GGML_STATUS_SUCCESS,"compute");
   ggml_backend_tensor_get(result,out.data(),0,out.size()*sizeof(float));
   ggml_backend_tensor_get(key_source,key_out.data(),0,key_out.size()*sizeof(float));
   ggml_backend_tensor_get(value,v_out.data(),0,v_out.size()*sizeof(float));
   for(size_t i=0;i<a.size();++i){
    require(key_out[i]==a[i]+2.f,"post-compute key overwritten");
    require(v_out[i]==(a[i]+2.f)*2.f,"post-compute value overwritten");
    require(out[i]==(a[i]+2.f)*2.f+40.f,"graph output differs");
   }
   ggml_backend_tensor_copy(key_source,dest);
   std::vector<float> copied(4096);ggml_backend_tensor_get(cache,copied.data(),0,copied.size()*sizeof(float));
   for(size_t i=0;i<copied.size();++i)require(copied[i]==(i>=1024&&i<2048?key_out[i-1024]:-7.f),"KV prefix or suffix changed");
  }
  ggml_gallocr_free(allocator);ggml_backend_buffer_free(persistent);ggml_free(c);
 }
 ggml_backend_free(backend);
 std::cout<<"real graph, post-compute KV, persistent footprint, rollback and retry passed\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
'''


@pytest.mark.parametrize('mutation',[None,'all_tensors','retain','rollback','view_failure'])
def test_real_ggml_storage_and_compiling_mutations(tmp_path,mutation):
    header=HEADER.read_text()
    if mutation=='all_tensors':header=header.replace('t->op == GGML_OP_NONE','true')
    elif mutation=='retain':header=header.replace('ggml_set_output(tensor);','(void)tensor;')
    elif mutation=='rollback':header=header.replace('item.first->data = nullptr; item.first->buffer = nullptr;','(void)item;')
    elif mutation=='view_failure':header=header.replace('if (ggml_backend_view_init(tensor) == GGML_STATUS_SUCCESS) return true;','ggml_backend_view_init(tensor); return true;')
    (tmp_path/'persistent_graph_storage.h').write_text(header)
    (tmp_path/'probe.cpp').write_text(PROBE)
    libs=[BUILD/'src/libggml.a',BUILD/'src/libggml-cpu.a',BUILD/'src/libggml-base.a']
    assert all(p.is_file() for p in libs),'build the exact ggml-storage-proof-20260913-r1 first'
    build=subprocess.run([shutil.which('clang++'),'-std=c++17','-Wall','-Wextra','-Werror','-I',str(ENGINE/'external/ggml/include'),str(tmp_path/'probe.cpp'),*map(str,libs),'-o',str(tmp_path/'probe')],capture_output=True,text=True)
    assert build.returncode==0,build.stderr
    run=subprocess.run([str(tmp_path/'probe')],capture_output=True,text=True,timeout=20)
    if mutation is None:assert run.returncode==0,run.stderr
    else:
        assert run.returncode==1,run.stderr
        expected={'all_tensors':'graph intermediates allocated permanently','retain':'post-compute key overwritten','rollback':'partial binding survived failure','view_failure':'view failure reported success'}
        assert expected[mutation] in run.stderr


def test_storage_change_preserves_graph_math_and_transfer_geometry():
    original=(ENGINE/'src/models/pocket_tts/flow_lm.cpp').read_bytes()
    changed=persistent_graph_storage(original)
    for begin,end in ((b'        auto x = modules::LinearModule',b'        params_buffer_ ='),
                      (b'    void build_transfer_views()',b'    void ensure_step_graph_allocated()')):
        # The allocation delta follows arithmetic, but inserts its own branch.
        old=original.split(begin)[1].split(end)[0]
        assert old in changed
    assert original.split(b'    void apply_prompt_from_state(')[1].split(b'    FlowLMStepResult run_in_place')[0] in changed
    assert b'for (const auto & key : keys_) native_retain_graph_value(key.tensor);' in changed
    assert b'for (const auto & value : values_) native_retain_graph_value(value.tensor);' in changed
    with pytest.raises(ValueError):persistent_graph_storage(original+b'changed')
