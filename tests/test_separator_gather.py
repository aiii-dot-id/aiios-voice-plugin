"""ONNX index normalization semantics; no model, microphone or GPU required."""
import copy
import unittest

import numpy as np
import onnx
import onnxruntime as ort

from scripts.export_mossformer2_separator import explicit_last_index_gathers


def graph(indices, axis=0, input_shape=('length', 4)):
    indices = np.asarray(indices, dtype=np.int64)
    output_shape = list(indices.shape)+list(input_shape[1:])
    g = onnx.helper.make_graph([
        onnx.helper.make_node('Gather', ['x', 'indices'], ['y'], axis=axis)],
        'index-proof', [onnx.helper.make_tensor_value_info('x', onnx.TensorProto.FLOAT, input_shape)],
        [onnx.helper.make_tensor_value_info('y', onnx.TensorProto.FLOAT, output_shape)],
        [onnx.numpy_helper.from_array(indices, 'indices')])
    return onnx.helper.make_model(g, opset_imports=[onnx.helper.make_opsetid('', 17)], ir_version=8)


def session(model):
    onnx.checker.check_model(model)
    return ort.InferenceSession(model.SerializeToString(), providers=['CPUExecutionProvider'])


class SeparatorGatherTests(unittest.TestCase):
    def test_scalar_vector_and_matrix_indices(self):
        for indices in (-1, [-1], [[-1], [-1]]):
            original = graph(indices)
            converted = copy.deepcopy(original)
            self.assertEqual(explicit_last_index_gathers(converted), 1)
            baseline, candidate = session(original), session(converted)
            for length in (1, 3, 17):
                x = np.arange(length*4, dtype=np.float32).reshape(length, 4)
                np.testing.assert_array_equal(baseline.run(None, {'x': x})[0],
                                              candidate.run(None, {'x': x})[0])

    def test_empty_axis_still_refuses(self):
        original = graph([-1]); converted = copy.deepcopy(original)
        explicit_last_index_gathers(converted)
        for model in (original, converted):
            with self.assertRaises(ort.capi.onnxruntime_pybind11_state.InvalidArgument):
                session(model).run(None, {'x': np.empty((0, 4), dtype=np.float32)})

    def test_other_indices_not_reinterpreted(self):
        for indices in ([-2], [-1, 0], [0], []):
            model = graph(indices); before = model.SerializeToString()
            self.assertEqual(explicit_last_index_gathers(model), 0)
            self.assertEqual(model.SerializeToString(), before)
        model = graph([-1], axis=1)
        self.assertEqual(explicit_last_index_gathers(model), 0)

    def test_constant_node_and_collision(self):
        model = graph([-1])
        tensor = model.graph.initializer.pop()
        model.graph.node.insert(0, onnx.helper.make_node('Constant', [], ['indices'], value=tensor))
        self.assertEqual(explicit_last_index_gathers(model), 1)
        np.testing.assert_array_equal(session(model).run(None, {'x': np.arange(12, dtype=np.float32).reshape(3, 4)})[0],
                                      np.array([[8, 9, 10, 11]], dtype=np.float32))
        model = graph([-1])
        model.graph.initializer.append(onnx.numpy_helper.from_array(np.array(0), 'aii_last_index_0_axis'))
        with self.assertRaises(ValueError): explicit_last_index_gathers(model)


if __name__ == '__main__':
    unittest.main()
