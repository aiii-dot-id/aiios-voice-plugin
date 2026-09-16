"""Byte-exact prefix/tail tests use allocated ggml tensors, not list mocks."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'artifacts/native-pocket-source-20260910-r2/audio.cpp-3174e6b26f11a0e39b4f150961dce98f43ba860d'
BUILD = ROOT / '.build/ggml-storage-proof-20260913-r1'
HEADER = ROOT / 'runtime/native_pocket/gpu_cache_prefix.h'
PROBE = r'''
#include "ggml-cpu.h"
#include "ggml-alloc.h"
#include "gpu_cache_prefix.h"
#include <iostream>
#include <algorithm>
#include <cstdint>
void require(bool yes,const char* why){if(!yes)throw std::runtime_error(why);}
int main(){try{
 auto* context=ggml_init({1024*1024,nullptr,true});require(context,"context");
 auto* tensor=ggml_new_tensor_1d(context,GGML_TYPE_F32,1024);
 auto* device=ggml_backend_cpu_init();require(device,"backend");
 auto* buffer=ggml_backend_alloc_ctx_tensors(context,device);require(buffer,"allocation");
 require(!native_is_vulkan_cache(tensor),"CPU incorrectly selected");
 require(!native_is_vulkan_cache(nullptr),"missing tensor selected");
 for(size_t count:{size_t(0),size_t(1),size_t(255),size_t(1024),size_t(3),size_t(0)}){
  std::vector<float> sentinel(1024,-913.25f),prefix(count),expected(1024,0.f),actual(1024);
  for(size_t i=0;i<count;++i)prefix[i]=float(i+7)*-.125f;
  if(count)prefix[0]=-0.f; // signed zero is payload, not disposable padding
  std::copy(prefix.begin(),prefix.end(),expected.begin());
  ggml_backend_tensor_set(tensor,sentinel.data(),0,4096);
  native_upload_cache_prefix(tensor,prefix,1024);
  ggml_backend_tensor_get(tensor,actual.data(),0,4096);
  require(std::memcmp(actual.data(),expected.data(),4096)==0,"prefix or zero tail differs");
 }
 bool caught=false;
 try{native_upload_cache_prefix(tensor,std::vector<float>(1025),1024);}catch(const std::runtime_error&){caught=true;}
 require(caught,"oversized prefix accepted");caught=false;
 try{native_upload_cache_prefix(tensor,{},1023);}catch(const std::runtime_error&){caught=true;}
 require(caught,"wrong capacity accepted");
 ggml_backend_buffer_free(buffer);ggml_free(context);ggml_backend_free(device);
 std::cout<<"real tensor: empty/full/shorter prefixes, signed zero, dirty tails, bounds and CPU dispatch passed\n";
 return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
'''


@pytest.mark.parametrize('mutation', [None, 'tail', 'prefix', 'offset'])
def test_actual_tensor_import_and_compiled_mutations(tmp_path, mutation):
    header = HEADER.read_text()
    if mutation == 'tail':
        header = header.replace('if (tail) ggml_backend_tensor_memset(tensor, 0, copied, tail);', '(void)tail;')
    elif mutation == 'prefix':
        header = header.replace('if (copied) ggml_backend_tensor_set(tensor, prefix.data(), 0, copied);', '(void)copied;')
    elif mutation == 'offset':
        header = header.replace('if (tail) ggml_backend_tensor_memset(tensor, 0, copied, tail);',
                                'if (tail > 4) ggml_backend_tensor_memset(tensor, 0, copied + 4, tail - 4);')
    (tmp_path / HEADER.name).write_text(header)
    (tmp_path / 'probe.cpp').write_text(PROBE)
    build = subprocess.run([shutil.which('clang++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-I', str(ENGINE / 'external/ggml/include'), str(tmp_path / 'probe.cpp'),
                            *map(str, (BUILD / 'src' / name for name in ('libggml.a', 'libggml-cpu.a', 'libggml-base.a'))),
                            '-o', str(tmp_path / 'probe')], capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(tmp_path / 'probe')], capture_output=True, text=True, timeout=20)
    if mutation is None: assert run.returncode == 0, run.stderr
    else:
        assert run.returncode == 1, run.stderr
        assert 'prefix or zero tail differs' in run.stderr


@pytest.mark.parametrize('damage', [None, 'validity', 'fallback', 'prefix', 'header'])
def test_import_source_remains_exact_except_for_the_gpu_copy(damage):
    from scripts.stage_native_single_prepare import gpu_prefix_import
    from scripts.audit_native_single_prepare import verify_prefix_source
    old = (ENGINE / 'src/framework/runtime/kv_cache.cpp').read_bytes()
    new = gpu_prefix_import(old)
    header = HEADER.read_bytes()
    verify_prefix_source(old,new,header)
    if damage == 'validity': new = new.replace(b'valid_steps_ = state_steps;',b'valid_steps_ = state_steps+1;')
    elif damage == 'fallback': new = new.replace(b'} else if (cache_steps_ > 0)',b'} else if (false)')
    elif damage == 'prefix': new = new.replace(b'source.key, cache.import_key_scratch.size()',b'source.value, cache.import_key_scratch.size()')
    elif damage == 'header': header += b'// changed\n'
    else: return
    with pytest.raises(AssertionError): verify_prefix_source(old,new,header)


@pytest.mark.parametrize('damage', [None,'missing','reset','no_gain'])
def test_counter_evidence_cannot_claim_an_unused_fast_path(damage):
    from scripts.audit_native_single_prepare import prefix_transfers
    runs=[]
    for arm in ('baseline','candidate','candidate','baseline'):
        rows=[{} for _ in range(5)]
        if arm=='candidate':
            for step,index in enumerate((0,1,2,4)):
                rows[index]['cache_transfer']={'imports':step*12,'uploaded_bytes':step*1024,'cleared_bytes':step*4096}
        runs.append({'arm':arm,'rows':rows})
    assert len(prefix_transfers(runs,.5))==6
    if damage=='missing': runs[1]['rows'][1]['cache_transfer']['imports']=0
    elif damage=='reset': runs[1]['rows'][2]['cache_transfer']['cleared_bytes']=4
    elif damage=='no_gain': runs[1]['rows'][1]['cache_transfer']['uploaded_bytes']=8192
    else: return
    with pytest.raises(AssertionError): prefix_transfers(runs,.5)
