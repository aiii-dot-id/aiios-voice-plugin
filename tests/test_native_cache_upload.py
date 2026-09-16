"""Deferred uploads into real ggml tensors, plus the actual cache importer."""
import shutil
import subprocess
import zipfile

import pytest

from scripts.cache_import_uploads import rewrite
from tests import test_native_pinned_upload as pinned
from tests import test_native_shared_import_scratch as shared

ROOT = shared.ROOT
ARCHIVE = ROOT/'deliverables/native-cache-import-session-windows-20260914-r1/transfer/source.zip'


def changed():
    with zipfile.ZipFile(ARCHIVE) as z:
        return rewrite(*(z.read('tts/original-'+n) for n in
                         ('kv_cache.cpp', 'flow_lm.cpp', 'kv_cache.h', 'CMakeLists.txt')))


def build_run(tmp, sources, includes=(), libraries=None):
    if libraries is None:
        libraries = [pinned.BUILD/'src'/n for n in ('libggml.a','libggml-cpu.a','libggml-base.a')]
    command = [shutil.which('clang++'), '-std=c++17', '-O1', '-I', str(tmp),
               '-I', str(shared.ENGINE/'include'), '-I', str(shared.ENGINE/'external/ggml/include'),
               *includes, *map(str, sources), *map(str, libraries), '-framework', 'Accelerate',
               '-pthread', '-o', str(tmp/'probe')]
    build = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert build.returncode == 0, build.stderr
    return subprocess.run([str(tmp/'probe')], capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize('mutation', [None, 'discard-final-group', 'stale-counter', 'missing-fence'])
def test_multiple_groups_own_reused_scratch_and_flush_tail(tmp_path, mutation):
    header = (ROOT/'runtime/native_pocket/cache_upload_batch.h').read_text()
    underlying = pinned.HEADER.read_text()
    if mutation == 'discard-final-group': header = header.replace('batch_->finish();', '(void)batch_;')
    if mutation == 'stale-counter': header = header.replace('            bytes_ = 0;', '            (void)bytes_;')
    if mutation == 'missing-fence': underlying = underlying.replace('ggml_backend_synchronize(backend_);', '(void)backend_;')
    (tmp_path/'cache_upload_batch.h').write_text(header)
    (tmp_path/'pinned_upload_batch.h').write_text(underlying)
    start = pinned.PROBE[:pinned.PROBE.index('int main()')]
    start = start.replace('#include "pinned_upload_batch.h"', '#include "cache_upload_batch.h"')
    main = r'''
int main(){try{
 auto b=ggml_backend_cpu_init();auto ctx=ggml_init({1048576,nullptr,true});
 std::vector<ggml_tensor*> tensors;
 for(int i=0;i<7;++i)tensors.push_back(ggml_new_tensor_1d(ctx,GGML_TYPE_F32,16));
 auto mem=ggml_backend_alloc_ctx_tensors(ctx,b);require(mem,"memory");
 for(int pass=0;pass<3;++pass){
  int before=fences;NativeCacheUploadBatch batch(b,128);std::vector<float> scratch(16);
  for(int i=0;i<7;++i){
   scratch.assign(16,float(10*pass+i));scratch[0]=-0.f;
   batch.f32(tensors[i],scratch);scratch.assign(16,-99.f);
   require(pending.empty(),"group escaped without fence");
   require(fences==before+i/2,"group budget or flush count wrong");
  }
  batch.finish();require(fences==before+4&&pending.empty(),"final group not completed");
  batch.finish();require(fences==before+4,"repeat finish transferred twice");
  for(int i=0;i<7;++i){std::vector<float> expected(16,float(10*pass+i)),actual(16);expected[0]=-0.f;
   ggml_backend_tensor_get(tensors[i],actual.data(),0,64);
   require(std::memcmp(expected.data(),actual.data(),64)==0,"reused scratch changed cache bytes");}
 }
 bool caught=false;try{NativeCacheUploadBatch batch(b,32);batch.f32(tensors[0],std::vector<float>(16));}
 catch(const std::invalid_argument&){caught=true;}require(caught,"oversized tensor not refused");
 caught=false;try{NativeCacheUploadBatch batch(b,0);}catch(const std::invalid_argument&){caught=true;}require(caught,"zero budget accepted");
 caught=false;try{NativeCacheUploadBatch batch(nullptr);}catch(const std::invalid_argument&){caught=true;}require(caught,"null backend accepted");
 inject=true;int before=fences,freed=frees;caught=false;
 try{NativeCacheUploadBatch batch(b,128);batch.f32(tensors[0],std::vector<float>(16,7.f));batch.finish();}
 catch(const std::runtime_error&){caught=true;}
 require(caught&&pending.empty()&&fences==before+1&&frees==freed+1,"failed group kept pending transfers");
 ggml_backend_buffer_free(mem);ggml_free(ctx);ggml_backend_free(b);
 std::cout<<"bounded groups, copied scratch, final tail and failure retirement PASS\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
'''
    (tmp_path/'probe.cpp').write_text(start+main)
    result = build_run(tmp_path, [tmp_path/'probe.cpp'])
    if mutation:
        assert result.returncode != 0, 'compiling mutation survived'
        expected = {'discard-final-group':'group budget or flush count wrong',
                    'stale-counter':'group budget or flush count wrong',
                    'missing-fence':'staging freed before completion'}[mutation]
        assert expected in result.stderr, result.stderr
    else: assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('mutation', [None, 'missing-finish', 'wrong-value', 'stale-tail'])
def test_actual_importer_keeps_prefixes_tails_and_legacy_abi(tmp_path, mutation):
    kv, _, header, _ = changed()
    if mutation == 'missing-finish': kv = kv.replace(b'if (uploads) uploads->finish();', b'if (uploads) (void)uploads;')
    if mutation == 'wrong-value': kv = kv.replace(b'uploads->f32(cache.value_tensor.tensor, scratch.import_value_scratch)', b'uploads->f32(cache.value_tensor.tensor, scratch.import_key_scratch)')
    if mutation == 'stale-tail': kv = kv.replace(b'std::fill(scratch.import_key_scratch.begin(), scratch.import_key_scratch.end(), 0.0F);', b'(void)scratch;')
    hp = tmp_path/'engine/framework/runtime/kv_cache.h';hp.parent.mkdir(parents=True);hp.write_bytes(header)
    (tmp_path/'kv.cpp').write_bytes(kv)
    for name in ('cache_upload_batch.h','pinned_upload_batch.h'):
        shutil.copyfile(ROOT/'runtime/native_pocket'/name,tmp_path/name)
    # CPU tensor storage with deliberately deferred transfers. This tests the
    # real rewritten importer; Windows/Vulkan performance is a separate gate.
    hooks = r'''
#include "ggml-backend.h"
ggml_backend_buffer_type_t host(ggml_backend_dev_t);
void queue(ggml_backend_t,ggml_tensor*,const void*,size_t,size_t);
void fence(ggml_backend_t);
void freebuf(ggml_backend_buffer_t);
#define ggml_backend_dev_host_buffer_type host
#define ggml_backend_tensor_set_async queue
#define ggml_backend_synchronize fence
#define ggml_backend_buffer_free freebuf
'''
    (tmp_path/'hooks.h').write_text(hooks)
    functions = pinned.PROBE[pinned.PROBE.index('struct Transfer'):pinned.PROBE.index('#define ggml_backend_dev_host_buffer_type')]
    probe = '#undef ggml_backend_dev_host_buffer_type\n#undef ggml_backend_tensor_set_async\n#undef ggml_backend_synchronize\n#undef ggml_backend_buffer_free\n'+shared.PROBE
    probe = probe.replace('int main(){try{', functions+'\nint main(){try{')
    probe = probe.replace('for(int layers:{1,3,6})', 'for(bool batched:{false,true}) for(int layers:{1,3,6})')
    probe = probe.replace('   cache.import_state(state);', '''   int before=fences;
   if(batched) cache.import_state(state,backend); else cache.import_state(state);
   need(pending.empty()&&fences==before+(batched?1:0),"import returned before full upload completion");''')
    (tmp_path/'probe.cpp').write_text(probe)
    libs=[shared.BUILD/'libengine_runtime.a',*(shared.BUILD/'ggml/src'/n for n in
          ('libggml.a','libggml-cpu.a','ggml-blas/libggml-blas.a','libggml-base.a'))]
    result=build_run(tmp_path,[tmp_path/'probe.cpp',tmp_path/'kv.cpp'],['-include',str(tmp_path/'hooks.h')],libs)
    if mutation:
        assert result.returncode == 1, result.stderr
        expected = 'import returned before full upload completion' if mutation=='missing-finish' else 'cross-layer prefix, signed zero or dirty tail differs'
        assert expected in result.stderr, result.stderr
    else: assert result.returncode == 0, result.stderr
