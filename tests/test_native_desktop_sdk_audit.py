import pytest

from scripts.audit_native_enrollment_desktops import bound_source_files


def test_source_binding_cannot_pass_vacuously():
    complete={n: {'sha256': '0'*64} for n in (
        'runtime/native/session/worker.cpp',
        'runtime/native_uid/speaker_registry.cpp',
        'plugin/native/main.go', 'plugin/native/snapshot.go',
        'plugin/native/enrollment.go')}
    assert set(bound_source_files(complete)) == set(complete)
    for name in complete:
        damaged=complete.copy();damaged.pop(name)
        with pytest.raises(ValueError):bound_source_files(damaged)
    with pytest.raises(ValueError):bound_source_files({'plugin/sdk-source.json': {}})
