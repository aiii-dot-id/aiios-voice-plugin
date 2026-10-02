"""Explicit Core ML packaging contracts; requires coremltools, never skips."""
import copy
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
import coremltools as ct
import numpy as np
from coremltools.libmilstoragepython import _BlobStorageReader as Reader
from coremltools.libmilstoragepython import _BlobStorageWriter as Writer
from scripts.stage_coreml_separator import cut_model,compact_weights


def fixture(count=24):
    spec=ct.proto.Model_pb2.Model();spec.specificationVersion=6
    fn=spec.mlProgram.functions['main'];fn.opset='CoreML5'
    inp=fn.inputs.add();inp.name='pcm';inp.type.tensorType.dataType=ct.proto.MIL_pb2.FLOAT32
    inp.type.tensorType.rank=2
    block=fn.block_specializations[fn.opset]
    previous='pcm'
    def operation(kind,name,inputs):
        op=block.operations.add();op.type=kind
        output=op.outputs.add();output.name=name;output.type.tensorType.dataType=ct.proto.MIL_pb2.FLOAT32
        output.type.tensorType.rank=3
        for n,value in enumerate(inputs):op.inputs[f'x{n}'].arguments.add().name=value
    for i in range(count):
        operation('conv',f'conv2_{i}',[previous])
        operation('transpose',f't_{i}',[f'conv2_{i}'])
        operation('add',f'boundary_{i}',[f't_{i}',previous]);previous=f'boundary_{i}'
    operation('identity','sources',[previous]);block.outputs.append('sources')
    desc=spec.description.input.add();desc.name='pcm'
    desc.type.multiArrayType.dataType=ct.proto.FeatureTypes_pb2.ArrayFeatureType.FLOAT32
    return spec


def owner(spec):
    return SimpleNamespace(get_spec=lambda:copy.deepcopy(spec))


class Stages(unittest.TestCase):
    def test_eight_closed_graphs_with_canonical_boundary_names(self):
        original=fixture();before=original.SerializeToString()
        stages=list(cut_model(owner(original)));self.assertEqual(len(stages),8)
        for index,spec in stages:
            fn=spec.mlProgram.functions['main'];block=fn.block_specializations[fn.opset]
            known={x.name for x in fn.inputs}
            self.assertEqual(known,{'pcm'} if index==0 else {'pcm','state_in'})
            for op in block.operations:
                for arg in op.inputs.values():
                    self.assertTrue({v.name for v in arg.arguments if v.name}<=known)
                known.update(o.name for o in op.outputs)
            self.assertEqual(list(block.outputs),['sources' if index==7 else 'state_out'])
            self.assertTrue(set(block.outputs)<=known)
            self.assertEqual(sum(op.type=='conv' for op in block.operations),3)
        self.assertEqual(original.SerializeToString(),before)

    def test_wrong_block_count_refused(self):
        with self.assertRaisesRegex(ValueError,'census'):list(cut_model(owner(fixture(23))))

    def test_wrong_interface_refused(self):
        spec=fixture();spec.mlProgram.functions['main'].inputs[0].name='other'
        with self.assertRaisesRegex(ValueError,'interface'):list(cut_model(owner(spec)))

    def test_nested_graph_refused(self):
        spec=fixture();spec.mlProgram.functions['main'].block_specializations['CoreML5'].operations[0].blocks.add()
        with self.assertRaisesRegex(ValueError,'nested'):list(cut_model(owner(spec)))

    def test_wrong_residual_boundary_refused(self):
        spec=fixture();spec.mlProgram.functions['main'].block_specializations['CoreML5'].operations[2].type='mul'
        with self.assertRaisesRegex(ValueError,'boundary'):list(cut_model(owner(spec)))

    def test_reserved_boundary_collision_refused(self):
        spec=fixture();spec.mlProgram.functions['main'].block_specializations['CoreML5'].operations[0].outputs[0].name='state_in'
        with self.assertRaisesRegex(ValueError,'occupied'):list(cut_model(owner(spec)))

    def test_weights_are_compacted_without_changing_bits(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);src=root/'source';dst=root/'output';src.mkdir();dst.mkdir()
            w=Writer(str(src/'weight.bin'));w.write_float_data(np.zeros(10000,np.float32))
            payload=np.array([1.,-0.,1e-30,-3.5],np.float32);offset=w.write_float_data(payload);del w
            spec=fixture();ops=spec.mlProgram.functions['main'].block_specializations['CoreML5'].operations
            for op in ops[:2]:
                v=op.attributes['val'];v.type.tensorType.dataType=ct.proto.MIL_pb2.FLOAT32
                v.type.tensorType.rank=1;v.type.tensorType.dimensions.add().constant.size=4
                v.blobFileValue.fileName='@model_path/weights/weight.bin';v.blobFileValue.offset=offset
            compact_weights(spec,src,dst)
            self.assertLess((dst/'weight.bin').stat().st_size,(src/'weight.bin').stat().st_size/2)
            self.assertEqual(ops[0].attributes['val'].blobFileValue.offset,ops[1].attributes['val'].blobFileValue.offset)
            actual=Reader(str(dst/'weight.bin')).read_float_data(ops[0].attributes['val'].blobFileValue.offset)
            self.assertEqual(actual.tobytes(),payload.tobytes())

    def test_reused_weight_with_contradictory_extent_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);src=root/'source';dst=root/'output';src.mkdir();dst.mkdir()
            w=Writer(str(src/'weight.bin'));offset=w.write_float_data(np.ones(4,np.float32));del w
            spec=fixture();ops=spec.mlProgram.functions['main'].block_specializations['CoreML5'].operations
            for op,size in zip(ops[:2],(4,3)):
                v=op.attributes['val'];v.type.tensorType.dataType=ct.proto.MIL_pb2.FLOAT32
                v.type.tensorType.dimensions.add().constant.size=size
                v.blobFileValue.fileName='@model_path/weights/weight.bin';v.blobFileValue.offset=offset
            with self.assertRaisesRegex(ValueError,'extent'):compact_weights(spec,src,dst)

    def test_weight_path_escape_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);src=root/'source';dst=root/'output';src.mkdir();dst.mkdir()
            w=Writer(str(src/'weight.bin'));offset=w.write_float_data(np.ones(4,np.float32));del w
            spec=fixture();v=spec.mlProgram.functions['main'].block_specializations['CoreML5'].operations[0].attributes['val']
            v.type.tensorType.dataType=ct.proto.MIL_pb2.FLOAT32
            v.blobFileValue.fileName='../elsewhere';v.blobFileValue.offset=offset
            with self.assertRaisesRegex(ValueError,'external weight'):compact_weights(spec,src,dst)


if __name__=='__main__':unittest.main()
