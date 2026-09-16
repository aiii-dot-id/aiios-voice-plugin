"""Two real graphs share KV: prompt scratch reuse cannot alter hot storage."""
from pathlib import Path
import shutil
import subprocess
import pytest
from scripts.stage_native_single_prepare import fixed_step_storage
from tests.test_native_persistent_storage import ROOT, ENGINE, BUILD

HEADER=ROOT/'runtime/native_pocket/fixed_step_graph_storage.h'
PROBE=r'''
#include "ggml.h"
#include "ggml-backend.h"
#include "ggml-cpu.h"
#include "ggml-alloc.h"
#include <iostream>
#include <stdexcept>
#include <vector>
int fail_binding=0,bindings=0;
ggml_status checked_alloc(ggml_backend_buffer_t b,ggml_tensor* t,void* p){
 const auto rc=ggml_backend_tensor_alloc(b,t,p);
 return ++bindings==fail_binding?GGML_STATUS_FAILED:rc;
}
#define ggml_backend_tensor_alloc checked_alloc
#include "fixed_step_graph_storage.h"
#undef ggml_backend_tensor_alloc
void require(bool v,const char* why){if(!v)throw std::runtime_error(why);}
int main(){try{
 auto* backend=ggml_backend_cpu_init();require(backend,"backend");
 ggml_backend_cpu_set_n_threads(backend,1);
 for(int failure:{0,2}){
  auto* c=ggml_init({16*1024*1024,nullptr,true});require(c,"context");
  auto* x=ggml_new_tensor_1d(c,GGML_TYPE_F32,1024);
  auto* y=ggml_new_tensor_1d(c,GGML_TYPE_F32,1024);
  auto* cache=ggml_new_tensor_1d(c,GGML_TYPE_F32,4096);
  auto* prefix=ggml_view_1d(c,cache,1024,0);
  auto* dest=ggml_view_1d(c,cache,1024,1024*sizeof(float));
  auto* cold=ggml_add(c,x,y);
  for(int i=0;i<20;++i)cold=ggml_add(c,cold,y);
  auto* prompt=ggml_new_graph_custom(c,256,false);
  ggml_build_forward_expand(prompt,ggml_cpy(c,cold,prefix));
  auto* key=ggml_add(c,prefix,y);
  auto* value=ggml_mul(c,key,y);
  auto* key_source=ggml_view_1d(c,key,1024,0);
  auto* result=value;
  for(int i=0;i<20;++i)result=ggml_add(c,result,y);
  auto* step=ggml_new_graph_custom(c,256,false);ggml_build_forward_expand(step,result);
  native_retain_graph_value(key_source);native_retain_graph_value(value);native_retain_graph_value(result);
  require(!native_allocate_fixed_step_storage(c,nullptr,backend),"missing graph accepted");
  bindings=0;fail_binding=failure;
  auto* fixed=native_allocate_fixed_step_storage(c,step,backend);
  if(failure){
   require(!fixed,"allocation failure reported success");
   require(!x->data&&!y->data&&!cache->data&&!key->data&&!value->data&&!result->data,"partial fixed bindings survived failure");
   fail_binding=0;bindings=0;fixed=native_allocate_fixed_step_storage(c,step,backend);
  }
  require(fixed&&x->data&&y->data&&cache->data,"persistent leaves absent");
  require(key->data&&value->data&&result->data,"hot intermediates not fixed");
  require(!cold->data,"prompt intermediates allocated permanently");
  require(native_bind_persistent_views(c)&&key_source->data&&prefix->data&&dest->data,"fixed views missing");
  auto* prompt_alloc=ggml_gallocr_new(ggml_backend_get_default_buffer_type(backend));
  auto* step_alloc=ggml_gallocr_new(ggml_backend_get_default_buffer_type(backend));
  require(ggml_gallocr_alloc_graph(prompt_alloc,prompt),"prompt allocation");
  require(ggml_gallocr_alloc_graph(step_alloc,step),"step allocation");
  require(ggml_gallocr_get_buffer_size(prompt_alloc,0)>0,"prompt has no scratch");
  require(ggml_gallocr_get_buffer_size(step_alloc,0)==0,"hot graph still needs scratch");
  auto* key_address=key->data;auto* value_address=value->data;auto* output_address=result->data;
  std::vector<float>a(1024),b(1024,2.f),state(4096,-7.f),out(1024),k(1024),v(1024),copied(4096);
  for(int pass=0;pass<3;++pass){
   for(size_t i=0;i<a.size();++i)a[i]=float(i+pass);
   ggml_backend_tensor_set(x,a.data(),0,a.size()*sizeof(float));
   ggml_backend_tensor_set(y,b.data(),0,b.size()*sizeof(float));
   ggml_backend_tensor_set(cache,state.data(),0,state.size()*sizeof(float));
   require(ggml_backend_graph_compute(backend,prompt)==GGML_STATUS_SUCCESS,"prompt compute");
   for(int repeat=0;repeat<3;++repeat){
    require(ggml_backend_graph_compute(backend,step)==GGML_STATUS_SUCCESS,"step compute");
    ggml_backend_tensor_get(key_source,k.data(),0,k.size()*sizeof(float));
    ggml_backend_tensor_get(value,v.data(),0,v.size()*sizeof(float));
    ggml_backend_tensor_get(result,out.data(),0,out.size()*sizeof(float));
    for(size_t i=0;i<a.size();++i){
     require(k[i]==a[i]+44.f,"post-compute key overwritten");
     require(v[i]==(a[i]+44.f)*2.f,"post-compute value overwritten");
     require(out[i]==(a[i]+44.f)*2.f+40.f,"output differs");
    }
    ggml_backend_tensor_copy(key_source,dest);
    ggml_backend_tensor_get(cache,copied.data(),0,copied.size()*sizeof(float));
    for(size_t i=0;i<copied.size();++i)require(copied[i]==(i<1024?a[i]+42.f:(i<2048?k[i-1024]:-7.f)),"KV prefix or suffix changed");
    require(key->data==key_address&&value->data==value_address&&result->data==output_address,"hot allocation moved");
   }
  }
  ggml_gallocr_free(step_alloc);ggml_gallocr_free(prompt_alloc);ggml_backend_buffer_free(fixed);ggml_free(c);
 }
 ggml_backend_free(backend);std::cout<<"two graphs, fixed hot storage, prompt scratch, KV and failure retry passed\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
'''


@pytest.mark.parametrize('mutation',[None,'no_hot','all_tensors','rollback'])
def test_real_graph_partition_and_compiling_mutations(tmp_path,mutation):
    header=HEADER.read_text()
    if mutation=='no_hot':header=header.replace('step_roots.count(t)','false')
    elif mutation=='all_tensors':header=header.replace('step_roots.count(t)','true')
    elif mutation=='rollback':header=header.replace('item.first->data = nullptr; item.first->buffer = nullptr;','(void)item;')
    (tmp_path/'fixed_step_graph_storage.h').write_text(header)
    (tmp_path/'persistent_graph_storage.h').write_bytes((ROOT/'runtime/native_pocket/persistent_graph_storage.h').read_bytes())
    (tmp_path/'probe.cpp').write_text(PROBE)
    libs=[BUILD/'src/libggml.a',BUILD/'src/libggml-cpu.a',BUILD/'src/libggml-base.a']
    run=subprocess.run([shutil.which('clang++'),'-std=c++17','-Wall','-Wextra','-Werror','-I',str(ENGINE/'external/ggml/include'),str(tmp_path/'probe.cpp'),*map(str,libs),'-o',str(tmp_path/'probe')],capture_output=True,text=True)
    assert run.returncode==0,run.stderr
    run=subprocess.run([str(tmp_path/'probe')],capture_output=True,text=True,timeout=20)
    if mutation is None:assert run.returncode==0,run.stderr
    else:
        assert run.returncode==1,run.stderr
        assert {'no_hot':'hot intermediates not fixed','all_tensors':'prompt intermediates allocated permanently','rollback':'partial fixed bindings survived failure'}[mutation] in run.stderr


def test_fixed_step_keeps_original_graph_math_copies_and_controls():
    original=(ENGINE/'src/models/pocket_tts/flow_lm.cpp').read_bytes()
    changed=fixed_step_storage(original)
    assert original.split(b'    void apply_prompt_from_state(')[1]==changed.split(b'    void apply_prompt_from_state(')[1]
    assert b'native_allocate_fixed_step_storage(ggml_ctx_, step_graph_, backend_)' in changed
    assert b'NV_HYBRID_STORAGE' in changed
    with pytest.raises(ValueError):fixed_step_storage(original+b'changed')
