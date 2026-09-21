"""Repack bound ONNX tensor bytes into downloadable chunks without changing them.

Only external-data locations change. Every tensor's bytes and every other
protobuf field must compare exactly. Execution on the repacked graphs is a
separate required gate; this tool does not inherit installed qualification.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil

from .prove_native_multitalker import verify_graphs
from .run_speaker_aware_reference import digest, write_json

CHUNK_BYTES = 512 * 1024 * 1024


def tensors(message):
    """Visit initializers and tensor attributes, including nested subgraphs."""
    if message.DESCRIPTOR.full_name == 'onnx.TensorProto':
        yield message
        return
    for field, value in message.ListFields():
        if field.type != field.TYPE_MESSAGE:
            continue
        if field.is_repeated:
            for item in value:
                yield from tensors(item)
        else:
            yield from tensors(value)


def external(tensor, root):
    from onnx import TensorProto
    if tensor.data_location != TensorProto.EXTERNAL:
        return None
    fields = {item.key: item.value for item in tensor.external_data}
    if len(fields) != len(tensor.external_data) or set(fields) - {'location', 'offset', 'length', 'checksum'}:
        raise ValueError('unknown or duplicate external tensor fields')
    name = fields.get('location', '')
    relative = PurePosixPath(name)
    if not name or relative.is_absolute() or '..' in relative.parts or '\\' in name:
        raise ValueError('external tensor path escapes graph')
    path = root.joinpath(*relative.parts)
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('external tensor is not contained regular data')
    offset = int(fields.get('offset', '0'))
    length = int(fields.get('length', str(path.stat().st_size - offset)))
    if offset < 0 or length <= 0 or offset + length > path.stat().st_size:
        raise ValueError('external tensor extent differs')
    return path, offset, length


def transfer(source, offset, count, target):
    hashed = hashlib.sha256()
    with source.open('rb') as stream:
        stream.seek(offset)
        while count:
            chunk = stream.read(min(count, 1024 * 1024))
            if not chunk:
                raise ValueError('tensor shortened during transfer')
            hashed.update(chunk)
            if target is not None:
                target.write(chunk)
            count -= len(chunk)
    return hashed.hexdigest()


def repack_graph(source, destination, chunk_bytes=CHUNK_BYTES):
    import onnx
    if chunk_bytes <= 0 or chunk_bytes > CHUNK_BYTES:
        raise ValueError('invalid download chunk bound')
    original = onnx.load(str(source / 'model.onnx'), load_external_data=False)
    changed = copy.deepcopy(original)
    destination.mkdir(parents=True, exist_ok=False)
    stream = None
    index = used = 0
    bindings = []
    try:
        for tensor in tensors(changed):
            extent = external(tensor, source)
            if extent is None:
                continue
            path, offset, count = extent
            if count > chunk_bytes:
                raise ValueError('one tensor exceeds the download chunk bound')
            if stream is None or used + count > chunk_bytes:
                if stream is not None:
                    stream.close()
                name = 'weights-%03d.bin' % index
                index += 1
                stream = (destination / name).open('xb')
                used = 0
            sha = transfer(path, offset, count, stream)
            del tensor.external_data[:]
            for key, value in (('location', name), ('offset', str(used)), ('length', str(count))):
                item = tensor.external_data.add()
                item.key, item.value = key, value
            bindings.append((count, sha))
            used += count
    finally:
        if stream is not None:
            stream.close()
    onnx.save_model(changed, str(destination / 'model.onnx'))
    restored = onnx.load(str(destination / 'model.onnx'), load_external_data=False)
    before, after = list(tensors(original)), list(tensors(restored))
    if len(before) != len(after):
        raise ValueError('tensor census changed')
    checked = 0
    for left, right in zip(before, after):
        extent = external(right, destination)
        if extent is None:
            if left != right:
                raise ValueError('inline tensor changed')
            continue
        path, offset, count = extent
        if (count, transfer(path, offset, count, None)) != bindings[checked]:
            raise ValueError('external tensor changed')
        checked += 1
        del right.external_data[:]
        right.external_data.extend(left.external_data)
    if checked != len(bindings) or restored != original:
        raise ValueError('model changed beyond external tensor locations')
    return {'tensor_count': checked, 'weight_files': index, 'tensor_bytes_identical': True,
            'non_location_fields_identical': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    original = verify_graphs(source)
    source_binding = digest(source / 'result.json')
    args.out.mkdir(parents=True, exist_ok=False)
    rows = {row['name']: repack_graph(source / row['name'], args.out / row['name'])
            for row in original['graphs']}
    shutil.copyfile(source / 'tokens.json', args.out / 'tokens.json')
    result = copy.deepcopy(original)
    result.update(compaction={'source_inventory_sha256': source_binding, 'graphs': rows,
                              'max_weight_file_bytes': CHUNK_BYTES,
                              'execution_revalidation_required': True},
                  artifacts={p.relative_to(args.out).as_posix(): {'sha256': digest(p), 'bytes': p.stat().st_size}
                             for p in sorted(args.out.rglob('*')) if p.is_file()})
    verify_graphs(source)
    if digest(source / 'result.json') != source_binding:
        raise ValueError('source inventory changed')
    write_json(args.out / 'result.json', result)
    verify_graphs(args.out)
    print(json.dumps({'byte_parity': True, 'files': len(result['artifacts']),
                      'source_inventory_sha256': source_binding}))


if __name__ == '__main__':
    main()
