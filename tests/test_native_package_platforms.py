import pytest
from scripts.package_common_native_checkpoint import platform_spec, runtime_archive_executable

@pytest.mark.parametrize('platform,arch,want,backend,suffix', [
    ('darwin','arm64','macos','cpu',''),
    ('linux','amd64','linux','vulkan',''),
    ('windows','amd64','windows','vulkan','.exe'),
])
def test_native_variant_keeps_its_platform(platform,arch,want,backend,suffix):
    s=platform_spec({'platform':platform,'arch':arch})
    package_arch='x86_64' if arch=='amd64' else arch
    assert s['platform']==want and s['arch']==package_arch and s['backend']==backend
    assert s['variant']==want+'-'+package_arch+'-native' and s['suffix']==suffix
    assert ('LibTorch' in s['libraries'])==(want=='windows')

def test_unknown_target_is_not_relabelled_macos():
    with pytest.raises(ValueError):platform_spec({'platform':'android','arch':'arm64'})

@pytest.mark.parametrize('executable',[False,True])
def test_runtime_inventory_matches_native_go_mode_contract(executable):
    row={'executable':executable}
    assert runtime_archive_executable(row,'windows') is False
    assert runtime_archive_executable(row,'linux')==executable
    assert runtime_archive_executable(row,'macos')==executable
