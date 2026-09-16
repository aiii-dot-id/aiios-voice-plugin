"""Real ggml tensors with deliberately delayed transfer completion and failure."""
from pathlib import Path
import subprocess
import pytest
from scripts.stage_native_single_prepare import batched_cache_copy
from scripts.audit_native_single_prepare import performance
from tests.test_native_persistent_storage import ROOT, ENGINE, BUILD
from tests.test_native_single_prepare import fixture

HEADER = ROOT / 'runtime/native_pocket/cache_copy_batch.h'
PROBE = r'''
#include "ggml.h"
#include "ggml-backend.h"
#include "ggml-cpu.h"
#include "ggml-alloc.h"
#include <vector>
#include <utility>
#include <stdexcept>
#include <iostream>
std::vector<std::pair<const ggml_tensor*,ggml_tensor*>> pending;
int fences=0,synchronous=0;bool inject=false;
void require(bool x,const char* s){if(!x)throw std::runtime_error(s);}
void queued(ggml_backend_t a,ggml_backend_t b,const ggml_tensor* s,ggml_tensor* d){
 require(a==b,"cross-backend transfer");pending.emplace_back(s,d);
 if(inject)throw std::runtime_error("injected enqueue failure");
}
void synced(ggml_backend_t){++fences;for(auto pair:pending)ggml_backend_tensor_copy(pair.first,pair.second);pending.clear();}
void immediate(const ggml_tensor* s,ggml_tensor* d){++synchronous;ggml_backend_tensor_copy(s,d);}
#define ggml_backend_tensor_copy_async queued
#define ggml_backend_synchronize synced
#define ggml_backend_tensor_copy immediate
#include "cache_copy_batch.h"
#undef ggml_backend_tensor_copy_async
#undef ggml_backend_synchronize
#undef ggml_backend_tensor_copy
int main(){try{
 auto* backend=ggml_backend_cpu_init();require(backend,"backend");
 auto* ctx=ggml_init({1024*1024,nullptr,true});require(ctx,"context");
 auto* keys=ggml_new_tensor_1d(ctx,GGML_TYPE_F32,128);
 auto* values=ggml_new_tensor_1d(ctx,GGML_TYPE_F32,128);
 auto* cache=ggml_new_tensor_1d(ctx,GGML_TYPE_F32,1024);
 auto* kd=ggml_view_1d(ctx,cache,128,256*sizeof(float));
 auto* vd=ggml_view_1d(ctx,cache,128,512*sizeof(float));
 auto* buf=ggml_backend_alloc_ctx_tensors(ctx,backend);require(buf,"allocation");
 std::vector<float> k(128),v(128),sentinel(1024,-7.f),out(1024);
 for(int pass=0;pass<3;++pass){
  for(size_t i=0;i<k.size();++i){k[i]=float(i+pass);v[i]=-float(i+pass);}
  ggml_backend_tensor_set(keys,k.data(),0,k.size()*4);ggml_backend_tensor_set(values,v.data(),0,v.size()*4);
  ggml_backend_tensor_set(cache,sentinel.data(),0,sentinel.size()*4);
  int before=fences;
  {NativeCacheCopyBatch batch(backend,true);batch.copy(keys,kd);batch.copy(values,vd);
   require(pending.size()==2&&fences==before&&synchronous==0,"copy was not batched");
   batch.finish();require(fences==before+1&&pending.empty(),"validity published before completion");
   batch.finish();require(fences==before+1,"duplicate fence");}
  ggml_backend_tensor_get(cache,out.data(),0,out.size()*4);
  for(size_t i=0;i<out.size();++i)require(out[i]==(i>=256&&i<384?k[i-256]:(i>=512&&i<640?v[i-512]:-7.f)),"KV or untouched cache bytes changed");
 }
 ggml_backend_tensor_set(cache,sentinel.data(),0,sentinel.size()*4);
 inject=true;int before=fences;bool caught=false;
 try{NativeCacheCopyBatch batch(backend,true);batch.copy(keys,kd);}catch(const std::runtime_error&){caught=true;}
 require(caught&&fences==before+1&&pending.empty(),"exception escaped with pending GPU writes");
 inject=false;before=fences;
 {NativeCacheCopyBatch batch(backend,false);batch.copy(keys,kd);batch.copy(values,vd);batch.finish();}
 require(fences==before&&synchronous==2&&pending.empty(),"non-Vulkan path changed");
 ggml_backend_buffer_free(buf);ggml_free(ctx);ggml_backend_free(backend);
 std::cout<<"batched KV, one fence, exact cache bytes, unwind and synchronous fallback PASS\n";
 return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
'''


@pytest.mark.parametrize('mutation', [None, 'blocking-copies', 'missing-fence', 'missing-unwind'])
def test_real_tensors_delayed_copy_completion_and_compiling_mutations(tmp_path, mutation):
    header = HEADER.read_text()
    if mutation == 'blocking-copies':
        header = header.replace('ggml_backend_tensor_copy_async(backend_, backend_, source, destination);',
                                'ggml_backend_tensor_copy(source, destination);')
    elif mutation == 'missing-fence':
        header = header.replace('ggml_backend_synchronize(backend_);', '(void)backend_;')
    elif mutation == 'missing-unwind':
        header = header.replace('~NativeCacheCopyBatch() noexcept { finish(); }', '~NativeCacheCopyBatch() noexcept {}')
    (tmp_path / 'cache_copy_batch.h').write_text(header)
    (tmp_path / 'probe.cpp').write_text(PROBE)
    command = ['clang++', '-std=c++17', '-O1', str(tmp_path / 'probe.cpp'), '-I', str(tmp_path),
               '-I', str(ENGINE / 'external/ggml/include'),
               str(BUILD / 'src/libggml.a'), str(BUILD / 'src/libggml-cpu.a'), str(BUILD / 'src/libggml-base.a'),
               '-framework', 'Accelerate', '-pthread', '-o', str(tmp_path / 'probe')]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stderr
    run = subprocess.run([str(tmp_path / 'probe')], capture_output=True, text=True, timeout=20)
    if mutation:
        assert run.returncode != 0, 'mutation survived'
        assert {'blocking-copies': 'copy was not batched', 'missing-fence': 'validity published before completion',
                'missing-unwind': 'exception escaped with pending GPU writes'}[mutation] in run.stderr
    else:
        assert run.returncode == 0, run.stderr


def test_batched_copy_changes_only_the_hot_transfer_block():
    path = ENGINE / 'src/models/pocket_tts/flow_lm.cpp'
    raw = path.read_bytes()
    changed = batched_cache_copy(raw)
    begin = raw.index(b'            for (size_t layer = 0; layer < key_sources_.size(); ++layer) {\n                ggml_backend_tensor_copy')
    end = raw.index(b'            attention_mask_buffer_[dst_slot] = 0.0F;', begin)
    without_include = changed.replace(b'#include "cache_copy_batch.h"\n', b'')
    assert without_include.startswith(raw[:begin]) and without_include.endswith(raw[end:])
    assert b'copies.finish(); // complete every KV write before publishing validity' in changed
    with pytest.raises(ValueError):
        batched_cache_copy(raw + b'changed')


def test_throughput_gate_requires_real_compute_gain_not_just_startup():
    runs, limits = fixture()
    limits.update(first_initial_ratio_max=1.05, first_initial_saved_seconds_min=-.05,
                  mean_compute_ratio_max=.90, mean_compute_saved_seconds_min=.1)
    assert not performance(runs, limits)['performance_gate_passed']
    for run in runs:
        if run['arm'] == 'candidate':
            for row in run['rows']:
                if row['type'] == 'synthesis': row['seconds'] = .3
    assert performance(runs, limits)['performance_gate_passed']
    runs[1]['rows'][2]['first_pcm_seconds'] = .6
    assert not performance(runs, limits)['performance_gate_passed']
