"""Cut a byte-bound FP32 separator into eight cancellable native stages.

Development packaging tool, not a plugin runtime. The input is an already
parity-qualified Core ML model; this does not convert or qualify its weights.
Use native execution to qualify the outputs, not Python chained predictions.
"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import tempfile


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def rename_operation(op,names):
    for output in op.outputs:
        output.name=names.get(output.name,output.name)
    for arg in op.inputs.values():
        for value in arg.arguments:
            value.name=names.get(value.name,value.name)


def compact_weights(spec,source_weights,destination):
    """Copy only referenced FP32 blobs, preserving their exact payload bytes."""
    import coremltools as ct
    from coremltools.libmilstoragepython import _BlobStorageReader as Reader
    from coremltools.libmilstoragepython import _BlobStorageWriter as Writer
    reader=Reader(str(source_weights/'weight.bin'))
    writer=Writer(str(destination/'weight.bin'));offsets={}
    fn=spec.mlProgram.functions['main']
    for op in fn.block_specializations[fn.opset].operations:
        values=list(op.attributes.values())
        for arg in op.inputs.values():
            values.extend(v.value for v in arg.arguments if v.HasField('value'))
        for value in values:
            if not value.HasField('blobFileValue'):
                continue
            blob=value.blobFileValue;shape=value.type.tensorType
            if blob.fileName!='@model_path/weights/weight.bin' or shape.dataType!=ct.proto.MIL_pb2.FLOAT32:
                raise ValueError('unsupported external weight reference')
            original=blob.offset
            payload=reader.read_float_data(original)
            if any(not d.HasField('constant') for d in shape.dimensions) or payload.size!=math.prod(d.constant.size for d in shape.dimensions):
                raise ValueError('weight payload extent differs')
            if original not in offsets:
                offsets[original]=writer.write_float_data(payload)
            blob.offset=offsets[original]
    del writer
    # Independent read-back catches truncated or incorrectly relocated blobs.
    copied=Reader(str(destination/'weight.bin'))
    for old,new in offsets.items():
        if reader.read_float_data(old).tobytes()!=copied.read_float_data(new).tobytes():
            raise ValueError('weight payload changed during compaction')


def cut_model(source):
    import coremltools as ct
    original=source.get_spec();fn=original.mlProgram.functions['main']
    ops=list(fn.block_specializations[fn.opset].operations)
    if [x.name for x in fn.inputs]!=['pcm'] or list(fn.block_specializations[fn.opset].outputs)!=['sources']:
        raise ValueError('separator graph interface differs')
    if any(op.blocks for op in ops):
        raise ValueError('separator graph has nested control flow')
    types={o.name:o.type for op in ops for o in op.outputs}
    if {'state_in','state_out'} & types.keys():
        raise ValueError('stage boundary names already occupied')
    ends=[]
    for i,op in enumerate(ops):
        if op.type=='conv' and any(o.name.startswith('conv2_') for o in op.outputs):
            rest=[x for x in ops[i+1:i+5] if x.type!='const']
            if len(rest)<2 or rest[0].type!='transpose' or rest[1].type!='add':
                raise ValueError('separator block boundary differs')
            ends.append(rest[1].outputs[0].name)
    if len(ends)!=24:
        raise ValueError('separator block census differs')
    previous=None
    for index,end in enumerate(ends[2::3][:-1]+['sources']):
        needed={end};kept=[]
        for op in reversed(ops):
            if not any(o.name in needed and o.name!=previous for o in op.outputs):
                continue
            kept.append(copy.deepcopy(op))
            for arg in op.inputs.values():
                needed.update(v.name for v in arg.arguments if v.name)
        spec=copy.deepcopy(original);f=spec.mlProgram.functions['main'];block=f.block_specializations[f.opset]
        output='sources' if index==7 else 'state_out'
        names={end:output}
        if previous:
            if previous not in needed:
                raise ValueError('disconnected stage')
            names[previous]='state_in'
            inp=f.inputs.add();inp.name='state_in';inp.type.CopyFrom(types[previous])
            desc=spec.description.input.add();desc.name='state_in'
            array=desc.type.multiArrayType;array.dataType=ct.proto.FeatureTypes_pb2.ArrayFeatureType.FLOAT32
            array.shape.extend([1,8999,512])
            for lo,hi in [(1,1),(3999,9999),(512,512)]:
                r=array.shapeRange.sizeRanges.add();r.lowerBound=lo;r.upperBound=hi
        for op in kept:
            rename_operation(op,names)
        del block.operations[:];block.operations.extend(reversed(kept))
        del block.outputs[:];block.outputs.append(output)
        del spec.description.output[:];desc=spec.description.output.add();desc.name=output
        desc.type.multiArrayType.dataType=ct.proto.FeatureTypes_pb2.ArrayFeatureType.FLOAT32
        yield index,spec
        previous=end


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model',type=Path,required=True)
    p.add_argument('--model-sha256',required=True)
    p.add_argument('--weights-sha256',required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    model=a.model/'Data/com.apple.CoreML/model.mlmodel'
    weights=a.model/'Data/com.apple.CoreML/weights/weight.bin'
    if digest(model)!=a.model_sha256 or digest(weights)!=a.weights_sha256:
        raise ValueError('source model bytes differ')
    import coremltools as ct
    source=ct.models.MLModel(str(a.model),skip_model_load=True)
    a.out.mkdir(mode=0o700,parents=True,exist_ok=False)
    for i,spec in cut_model(source):
        with tempfile.TemporaryDirectory(prefix='weights-',dir=a.out) as temporary:
            compact_weights(spec,Path(source.weights_dir),Path(temporary))
            ct.models.MLModel(spec,weights_dir=temporary,skip_model_load=True).save(str(a.out/f'stage-{i}.mlpackage'))
    if digest(model)!=a.model_sha256 or digest(weights)!=a.weights_sha256:
        raise ValueError('source model changed during staging')
    files={str(f.relative_to(a.out)):digest(f) for f in sorted(a.out.rglob('*')) if f.is_file()}
    with (a.out/'inventory.json').open('x') as f:
        json.dump({'input_model_sha256':a.model_sha256,'input_weights_sha256':a.weights_sha256,
                   'files':files,'qualified':False,'samples':[32000,80003],'sample_rate':16000},f,indent=2)


if __name__=='__main__':
    main()
