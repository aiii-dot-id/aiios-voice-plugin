import subprocess
import pytest
from tests.test_native_persistent_storage import ROOT, ENGINE, BUILD

HEADER=ROOT/'runtime/native_pocket/pinned_upload_batch.h'
PROBE=r'''
#include "ggml.h"
#include "ggml-backend.h"
#include "ggml-cpu.h"
#include "ggml-alloc.h"
#include <vector>
#include <stdexcept>
#include <iostream>
#include <cstring>
struct Transfer{ggml_tensor* tensor;const void* data;size_t offset;size_t size;};
std::vector<Transfer> pending;int fences=0,frees=0;bool inject=false,deny=false;
void require(bool x,const char* m){if(!x)throw std::runtime_error(m);}
ggml_backend_buffer_type_t host(ggml_backend_dev_t){return deny?nullptr:ggml_backend_cpu_buffer_type();}
void queue(ggml_backend_t,ggml_tensor*t,const void*p,size_t off,size_t n){pending.push_back({t,p,off,n});if(inject)throw std::runtime_error("enqueue injected");}
void fence(ggml_backend_t){++fences;for(auto e:pending)ggml_backend_tensor_set(e.tensor,e.data,e.offset,e.size);pending.clear();}
void freebuf(ggml_backend_buffer_t b){require(pending.empty(),"staging freed before completion");++frees;ggml_backend_buffer_free(b);}
#define ggml_backend_dev_host_buffer_type host
#define ggml_backend_tensor_set_async queue
#define ggml_backend_synchronize fence
#define ggml_backend_buffer_free freebuf
#include "pinned_upload_batch.h"
#undef ggml_backend_dev_host_buffer_type
#undef ggml_backend_tensor_set_async
#undef ggml_backend_synchronize
#undef ggml_backend_buffer_free
int main(){try{
 auto b=ggml_backend_cpu_init();require(b,"backend");auto ctx=ggml_init({1048576,nullptr,true});
 auto f=ggml_new_tensor_1d(ctx,GGML_TYPE_F32,16);auto i=ggml_new_tensor_1d(ctx,GGML_TYPE_I32,4);
 auto mem=ggml_backend_alloc_ctx_tensors(ctx,b);require(mem,"memory");
 for(int pass=0;pass<3;++pass){
  std::vector<float> vals(16,float(pass)+.75f);std::vector<int32_t> ids{pass,2,-4,3};int before=fences,freed=frees;
  {NativePinnedUploadBatch x(b);x.f32(f,vals);x.i32(i,ids);vals.assign(16,99.f);ids.assign(4,99);
   require(pending.empty()&&fences==before,"writes escaped before prepared batch");x.finish();
   require(pending.empty()&&fences==before+1&&frees==freed+1,"batch completion not exactly once");x.finish();require(fences==before+1,"double completion");
   bool caught=false;try{x.f32(f,vals);}catch(const std::invalid_argument&){caught=true;}require(caught,"append after completion accepted");}
  float out[16];int32_t idx[4];ggml_backend_tensor_get(f,out,0,sizeof(out));ggml_backend_tensor_get(i,idx,0,sizeof(idx));
  for(float v:out)require(v==float(pass)+.75f,"copied float payload changed");require(idx[0]==pass&&idx[1]==2&&idx[2]==-4&&idx[3]==3,"copied integer payload changed");
 }
 int before=fences,freed=frees;inject=true;bool caught=false;
 try{NativePinnedUploadBatch x(b);x.f32(f,std::vector<float>(16,7.f));x.i32(i,std::vector<int32_t>(4,9));x.finish();}catch(const std::runtime_error&){caught=true;}
 require(caught&&pending.empty()&&fences==before+1&&frees==freed+1,"unwind did not finish queued transfer");inject=false;
 deny=true;caught=false;before=fences;try{NativePinnedUploadBatch x(b);x.f32(f,std::vector<float>(16));x.finish();}catch(const std::runtime_error&){caught=true;}
 require(caught&&fences==before&&pending.empty(),"missing pinned provider hidden");deny=false;
 caught=false;try{NativePinnedUploadBatch x(b);x.f32(i,std::vector<float>(4));}catch(const std::invalid_argument&){caught=true;}require(caught,"wrong scalar type accepted");
 caught=false;try{NativePinnedUploadBatch x(b);x.i32(i,std::vector<int32_t>(3));}catch(const std::invalid_argument&){caught=true;}require(caught,"wrong extent accepted");
 ggml_backend_buffer_free(mem);ggml_free(ctx);ggml_backend_free(b);std::cout<<"pinned upload ownership, payload, completion and refusal PASS\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
'''

@pytest.mark.parametrize('mutation',[None,'missing-fence','release-first','drop-copy'])
def test_real_tensors_deferred_sources_and_failure(tmp_path,mutation):
    raw=HEADER.read_text()
    if mutation=='missing-fence': raw=raw.replace('ggml_backend_synchronize(backend_);','(void)backend_;')
    elif mutation=='release-first': raw=raw.replace('if (pending_) {','if (pinned_) { ggml_backend_buffer_free(pinned_); pinned_ = nullptr; }\n        if (pending_) {',1)
    elif mutation=='drop-copy': raw=raw.replace('std::memcpy(base, bytes_.data(), bytes_.size());','std::memset(base, 0, bytes_.size());')
    (tmp_path/'pinned_upload_batch.h').write_text(raw);(tmp_path/'probe.cpp').write_text(PROBE)
    built=subprocess.run(['clang++','-std=c++17','-O1',str(tmp_path/'probe.cpp'),'-I',str(tmp_path),'-I',str(ENGINE/'external/ggml/include'),
        *[str(BUILD/'src'/n) for n in ('libggml.a','libggml-cpu.a','libggml-base.a')],'-framework','Accelerate','-pthread','-o',str(tmp_path/'probe')],capture_output=True,text=True,timeout=60)
    assert built.returncode==0,built.stderr
    result=subprocess.run([str(tmp_path/'probe')],capture_output=True,text=True,timeout=20)
    if mutation:
        assert result.returncode != 0,'mutation survived'
        assert ('copied float payload changed' if mutation=='drop-copy' else 'staging freed before completion') in result.stderr
    else: assert result.returncode==0,result.stderr
