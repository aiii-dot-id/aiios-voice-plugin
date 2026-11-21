import base64
import hashlib
import queue
import threading

import pytest

from scripts.speaker_registry_test_host import RegistryBroker


class Host:
    def __init__(self):
        self.host_requests, self.write_lock = queue.Queue(), threading.Lock()


@pytest.fixture
def broker():
    b = RegistryBroker(Host())
    try:
        yield b
    finally:
        b.close()


def private(path):
    return dict(root='private', path=path)


def stage(broker, path, raw):
    return broker.answer(dict(operation='fs.write', target=private(path),
                              arguments=dict(data_b64=base64.b64encode(raw).decode(), append=False)))


def publish(broker, path, source, raw, **expect):
    args = {'from': source, 'sha256': hashlib.sha256(raw).hexdigest(), **(expect or {'expected_absent': True})}
    return broker.answer(dict(operation='fs.publish', target=private(path), arguments=args))


def read(broker, path):
    return broker.answer(dict(operation='fs.read', target=private(path),
                              arguments=dict(offset=0, length=65536, digest=True)))


UPLOAD = 'a' * 64


def test_the_heard_days_are_staged_published_and_read(broker):
    b = broker
    raw = b'{"schema":"heard","days":{}}'
    assert stage(b, f'uid/.heard-{UPLOAD}.pending', raw)['status'] == 'succeeded'
    assert publish(b, 'uid/speakers-heard.json', f'uid/.heard-{UPLOAD}.pending', raw)['status'] == 'succeeded'
    value = read(b, 'uid/speakers-heard.json')['operation_result']
    assert base64.b64decode(value['data_b64']) == raw and value['sha256'] == hashlib.sha256(raw).hexdigest()


def test_a_recovery_archive_is_staged_and_published_under_its_own_digest(broker):
    b = broker
    raw = b'{"schema":"recovery"}'
    digest = hashlib.sha256(raw).hexdigest()
    assert stage(b, f'uid/.recovery-{UPLOAD}.pending', raw)['status'] == 'succeeded'
    assert publish(b, f'uid/recovery-{digest}.json', f'uid/.recovery-{UPLOAD}.pending', raw)['status'] == 'succeeded'
    assert b.storage[f'uid/recovery-{digest}.json'] == raw


@pytest.mark.parametrize('path', [
    'uid/other.json', 'uid/speakers-heard.json.bak', f'uid/.heard-{UPLOAD.upper()}.pending', f'uid/.heard-{UPLOAD[:63]}.pending',
    f'uid/.recovery-{UPLOAD}.pending.x', f'uid/recovery-{UPLOAD}.json', 'uid/../uid/speakers-heard.json'])
def test_no_other_name_is_admitted(broker, path):
    with pytest.raises(AssertionError, match='unexpected private-store target'):
        stage(broker, path, b'x')


def test_a_staged_file_is_published_only_to_its_own_destination(broker):
    raw = b'{}'
    stage(broker, f'uid/.heard-{UPLOAD}.pending', raw)
    with pytest.raises(AssertionError):
        publish(broker, 'uid/speakers.json', f'uid/.heard-{UPLOAD}.pending', raw)
    stage(broker, f'uid/.speakers-{UPLOAD}.pending', raw)
    with pytest.raises(AssertionError):
        publish(broker, 'uid/speakers-heard.json', f'uid/.speakers-{UPLOAD}.pending', raw)
    stage(broker, f'uid/.recovery-{UPLOAD}.pending', raw)
    with pytest.raises(AssertionError):
        publish(broker, f'uid/recovery-{"b" * 64}.json', f'uid/.recovery-{UPLOAD}.pending', raw)


def test_a_published_file_is_never_written_or_a_recovery_archive_read_directly(broker):
    with pytest.raises(AssertionError):
        stage(broker, 'uid/speakers-heard.json', b'x')
    with pytest.raises(AssertionError):
        read(broker, f'uid/recovery-{"b" * 64}.json')
