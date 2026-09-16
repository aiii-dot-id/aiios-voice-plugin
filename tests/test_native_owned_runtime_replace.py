"""Compile real shared ownership and allocation-failure counterexamples."""
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / 'runtime/native_pocket/owned_runtime_replace.h'
PROBE = r'''
#include "owned_runtime_replace.h"
#include <iostream>
#include <string>
void require(bool yes,const char* why){if(!yes)throw std::runtime_error(why);}
struct Graph {
 int& alive; std::shared_ptr<int> weights;
 Graph(int& n,std::shared_ptr<int> w):alive(n),weights(std::move(w)){++alive;}
 ~Graph(){--alive;}
};
int main(){try{
 int alive=0;auto weights=std::make_shared<int>(42);
 auto cache=std::make_shared<Graph>(alive,weights);
 native_replace_owned_runtime(cache,[&]{
  require(alive==0,"old owned graph overlaps allocation");
  require(*weights==42,"weights retired");
  return std::make_shared<Graph>(alive,weights);
 });
 require(alive==1&&cache,"new graph missing");
 auto borrowed=cache;
 native_replace_owned_runtime(cache,[&]{
  require(alive==1&&borrowed->weights==weights,"borrowed graph retired");
  return std::make_shared<Graph>(alive,weights);
 });
 require(alive==2&&cache!=borrowed,"replacement reused borrowed graph");
 borrowed.reset();require(alive==1,"old graph leaked");
 borrowed=cache;
 bool threw=false;
 try{native_replace_owned_runtime(cache,[&]()->std::shared_ptr<Graph>{throw std::runtime_error("allocation failed");});}
 catch(const std::runtime_error& e){threw=std::string(e.what())=="allocation failed";}
 require(threw,"allocation failure swallowed");
 require(!cache,"stale graph retained under changed geometry");
 require(alive==1&&borrowed->weights==weights,"borrower damaged on failure");
 borrowed.reset();
 native_replace_owned_runtime(cache,[&]{return std::make_shared<Graph>(alive,weights);});
 require(cache&&alive==1,"empty cache retry failed");
 threw=false;
 try{native_replace_owned_runtime(cache,[]{return std::shared_ptr<Graph>{};});}
 catch(const std::runtime_error&){threw=true;}
 require(threw&&!cache&&alive==0,"null runtime accepted");
 require(weights.use_count()==1,"graph lifetime leak");
 std::cout<<"owned retirement, borrowed lifetime, failure, retry, null and weights passed\n";
 return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
'''


@pytest.mark.parametrize('mutation', [None, 'overlap', 'stale', 'null'])
def test_owned_replacement_and_compiling_mutations(tmp_path, mutation):
    raw = HEADER.read_text()
    if mutation == 'overlap':
        raw = raw.replace('if (cached.use_count() == 1) cached.reset();', '(void)cached;')
    elif mutation == 'stale':
        raw = raw.replace('        cached.reset();\n        throw;', '        throw;')
    elif mutation == 'null':
        raw = raw.replace('if (!replacement) throw std::runtime_error("replacement runtime is null");', '(void)replacement;')
    (tmp_path / HEADER.name).write_text(raw)
    (tmp_path / 'probe.cpp').write_text(PROBE)
    build = subprocess.run([shutil.which('clang++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            str(tmp_path / 'probe.cpp'), '-o', str(tmp_path / 'probe')], capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(tmp_path / 'probe')], capture_output=True, text=True, timeout=10)
    if mutation is None:
        assert run.returncode == 0, run.stderr
    else:
        assert run.returncode == 1, run.stderr
        assert {'overlap': 'old owned graph overlaps allocation',
                'stale': 'stale graph retained under changed geometry',
                'null': 'null runtime accepted'}[mutation] in run.stderr


@pytest.mark.parametrize('damage', [None, 'geometry', 'backend', 'helper', 'argument', 'parent'])
def test_exact_geometry_and_private_backend_seam(damage):
    from scripts.stage_native_single_prepare import owned_runtime_retirement
    from scripts.audit_native_single_prepare import verify_owned_source
    with zipfile.ZipFile(ROOT / 'deliverables/native-gpu-capacity-20260912-r1/payload.zip') as z:
        old = z.read('source/acoustic_model.cpp')
    new = owned_runtime_retirement(old)
    header = HEADER.read_bytes()
    assert b'runtime_cache_.prompt_capacity == prompt_capacity' in new
    verify_owned_source(old, new, header)
    if damage == 'geometry': new = new.replace(b'prompt_capacity == prompt_capacity', b'prompt_capacity >= prompt_capacity')
    elif damage == 'backend': new = new.replace(b'core::BackendType::Vulkan', b'core::BackendType::Cpu')
    elif damage == 'helper': header += b'// unbound change\n'
    elif damage == 'argument': new = new.replace(b'                total_cache_steps,', b'                total_cache_steps + 1,')
    elif damage == 'parent':
        with pytest.raises(ValueError): owned_runtime_retirement(old + b'changed')
        return
    else: return
    with pytest.raises(AssertionError): verify_owned_source(old, new, header)
