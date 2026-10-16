"""Exact contraction axes, dynamic time, batch isolation and strict refusal."""
import copy
import unittest

import numpy as np
import onnx
import onnxruntime as ort

from scripts.export_mossformer2_separator import explicit_attention_contractions


def graph(equation, ranks):
    equation = equation.replace(' ', '')
    helper = onnx.helper
    nodes = [helper.make_node('Einsum', ['a', 'b'], ['y'], equation=equation)]
    inputs = [helper.make_tensor_value_info(name, onnx.TensorProto.FLOAT,
              [f'{name}{axis}' for axis in range(rank)]) for name, rank in zip(('a', 'b'), ranks)]
    result_rank = len(equation.split('->')[1].replace('...', ''))
    if '...' in equation.split('->')[1]:
        result_rank += ranks[0]-len(equation.split(',')[0].replace('...', ''))
    output = helper.make_tensor_value_info('y', onnx.TensorProto.FLOAT,
              [f'y{axis}' for axis in range(result_rank)])
    return helper.make_model(helper.make_graph(nodes, 'attention', inputs, [output]),
                             opset_imports=[helper.make_opsetid('', 17)], ir_version=9)


class ContractionTests(unittest.TestCase):
    def check(self, equation, shapes):
        original = graph(equation, tuple(len(s) for s in shapes[0]))
        lowered = copy.deepcopy(original)
        self.assertEqual(explicit_attention_contractions(lowered), 1)
        self.assertEqual(original.graph.node[0].op_type, 'Einsum')
        self.assertFalse(any(n.op_type == 'Einsum' for n in lowered.graph.node))
        onnx.checker.check_model(lowered)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        session = ort.InferenceSession(lowered.SerializeToString(), sess_options=options,
                                       providers=['CPUExecutionProvider'])
        rng = np.random.default_rng(31)
        for left_shape, right_shape in shapes:
            left = rng.normal(size=left_shape).astype(np.float32)
            right = rng.normal(size=right_shape).astype(np.float32)
            expected = np.einsum(equation, left, right)
            actual = session.run(['y'], {'a': left, 'b': right})[0]
            self.assertEqual(actual.shape, expected.shape)
            np.testing.assert_allclose(actual, expected, rtol=2e-5, atol=2e-5)

    def test_outer_rotary(self):
        self.check('..., f -> ... f', [((1,), (7,)), ((19,), (5,))])
        self.check('i, j -> i j', [((1,), (3,)), ((31,), (11,))])

    def test_head_scaling(self):
        self.check('... d, h d -> ... h d', [((1, 5, 7), (4, 7)), ((2, 17, 11), (3, 11))])

    def test_local_attention(self):
        self.check('... i d, ... j d -> ... i j',
                   [((1, 2, 7, 11), (1, 2, 5, 11)), ((3, 4, 13, 7), (3, 4, 19, 7))])
        self.check('... i j, ... j d -> ... i d',
                   [((1, 2, 7, 5), (1, 2, 5, 11)), ((3, 4, 13, 19), (3, 4, 19, 7))])

    def test_global_attention_reduces_groups_and_frames_not_batches(self):
        self.check('b g n d, b g n e -> b d e',
                   [((1, 1, 1, 7), (1, 1, 1, 11)), ((3, 4, 19, 7), (3, 4, 19, 11))])

    def test_shared_matrix_does_not_expand_sequence_axes(self):
        self.check('b g n d, b d e -> b g n e',
                   [((1, 1, 1, 7), (1, 7, 11)), ((3, 4, 19, 7), (3, 7, 11))])

    def test_unknown_equation_refuses_without_mutation(self):
        model = graph('ij,jk->ik', (2, 2))
        before = model.SerializeToString()
        with self.assertRaisesRegex(ValueError, 'equation differs'):
            explicit_attention_contractions(model)
        self.assertEqual(model.SerializeToString(), before)

    def test_invalid_shared_rank_refuses(self):
        with self.assertRaisesRegex(ValueError, 'rank differs'):
            explicit_attention_contractions(graph('bgnd,bde->bgne', (3, 3)))

    def test_generated_name_collision_refuses(self):
        model = graph('...,f->...f', (1, 1))
        model.graph.initializer.append(onnx.numpy_helper.from_array(
            np.array([0], dtype=np.int64), 'aii_attention_0_axes_0'))
        with self.assertRaisesRegex(ValueError, 'name collides'):
            explicit_attention_contractions(model)


if __name__ == '__main__':
    unittest.main()
