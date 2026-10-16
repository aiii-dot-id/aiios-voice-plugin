"""Numerical export gate; run explicitly in the pinned model-export environment.

Requires torch, onnx and onnxruntime. It is separate from model-free package
contracts and does not skip when these dependencies are absent.
"""
import tempfile
import unittest
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import torch

from scripts.export_mossformer2_separator import accurate_group_norms, rank_stable_fsmns


class ReferenceFSMN(torch.nn.Module):
    """Small learned layers with the pinned upstream dimension operations."""
    def __init__(self):
        super().__init__()
        self.linear = torch.nn.Linear(8, 12)
        self.project = torch.nn.Linear(12, 8, bias=False)
        self.conv = torch.nn.Conv2d(8, 8, (3, 1), padding=(1, 0), groups=8)

    def forward(self, x):
        p = self.project(torch.nn.functional.relu(self.linear(x)))
        y = self.conv(p.unsqueeze(1).permute(0, 3, 2, 1))
        return x+y.permute(0, 3, 2, 1).squeeze()


class SeparatorRankTests(unittest.TestCase):
    def test_reference_and_parameters_unchanged(self):
        import copy
        original = torch.nn.Sequential(ReferenceFSMN()).eval()
        converted = copy.deepcopy(original)
        self.assertEqual(rank_stable_fsmns(converted, ReferenceFSMN), 1)
        self.assertIsInstance(original[0], ReferenceFSMN)
        for (k, v), (ck, cv) in zip(original.state_dict().items(), converted.state_dict().items()):
            self.assertEqual(k, ck)
            torch.testing.assert_close(v, cv, rtol=0, atol=0)
            self.assertNotEqual(v.data_ptr(), cv.data_ptr())
        for batch, frames in ((1, 1), (1, 32), (1, 8999), (2, 7)):
            x = torch.randn(batch, frames, 8)
            with torch.inference_mode():
                expected, actual = original(x), converted(x)
            self.assertEqual(actual.shape, x.shape)
            torch.testing.assert_close(actual, expected, rtol=0, atol=0)

    def test_onnx_preserves_dynamic_rank(self):
        import copy
        original = torch.nn.Sequential(ReferenceFSMN()).eval()
        converted = copy.deepcopy(original)
        rank_stable_fsmns(converted, ReferenceFSMN)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'fsmn.onnx'
            with torch.inference_mode():
                torch.onnx.export(converted, (torch.randn(1, 31, 8),), str(path),
                    input_names=['x'], output_names=['y'], dynamo=False,
                    dynamic_axes={'x': {0: 'batch', 1: 'frames'},
                                  'y': {0: 'batch', 1: 'frames'}}, opset_version=17)
            graph = onnx.shape_inference.infer_shapes(onnx.load(path))
            self.assertFalse(any(node.op_type == 'If' for node in graph.graph.node))
            for node in graph.graph.node:
                if node.op_type == 'Squeeze':
                    self.assertEqual(len(node.input), 2, 'axes must be explicit')
            session = ort.InferenceSession(str(path), providers=['CPUExecutionProvider'])
            for batch, frames in ((1, 1), (1, 128), (2, 17)):
                x = torch.randn(batch, frames, 8)
                with torch.inference_mode(): expected = original(x).numpy()
                actual = session.run(['y'], {'x': x.numpy()})[0]
                self.assertEqual(actual.shape, expected.shape)
                np.testing.assert_allclose(actual, expected, rtol=1e-5, atol=1e-6)


class SeparatorNormalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(4)
        torch.manual_seed(28)

    def check_norm(self, shape, groups, affine=True):
        original = torch.nn.Sequential(torch.nn.GroupNorm(groups, shape[1], affine=affine)).eval()
        if affine:
            with torch.no_grad():
                original[0].weight.uniform_(.5, 1.5)
                original[0].bias.uniform_(-.2, .2)
        before = {k: v.clone() for k, v in original.state_dict().items()}
        converted, count = accurate_group_norms(original)
        self.assertEqual(count, 1)
        self.assertIsInstance(original[0], torch.nn.GroupNorm)
        if affine:
            self.assertNotEqual(original[0].weight.data_ptr(), converted[0].weight.data_ptr())
        x = torch.randn(*shape)*.03+.1
        with torch.inference_mode():
            expected = original(x)
            actual = converted(x)
        self.assertEqual(actual.dtype, x.dtype)
        self.assertEqual(actual.shape, x.shape)
        torch.testing.assert_close(actual, expected, rtol=2e-5, atol=2e-5)
        for key, value in original.state_dict().items():
            torch.testing.assert_close(value, before[key], rtol=0, atol=0)

    def test_long_single_group(self):
        self.check_norm((1, 512, 8999), 1)

    def test_multiple_groups_and_batches(self):
        self.check_norm((2, 32, 8193), 8)

    def test_no_affine(self):
        self.check_norm((2, 16, 65), 4, affine=False)

    def test_rank_two_and_four(self):
        self.check_norm((2, 16), 4)
        self.check_norm((2, 16, 33, 35), 4)

    def test_recursive_copy_and_unchanged_modules(self):
        original = torch.nn.Sequential(torch.nn.ReLU(), torch.nn.Sequential(
            torch.nn.GroupNorm(1, 8), torch.nn.GroupNorm(2, 8)))
        converted, count = accurate_group_norms(original)
        self.assertEqual(count, 2)
        self.assertIsInstance(converted[0], torch.nn.ReLU)
        self.assertIsInstance(original[1][0], torch.nn.GroupNorm)

    def test_silence_remains_finite(self):
        model, _ = accurate_group_norms(torch.nn.Sequential(torch.nn.GroupNorm(1, 512)))
        with torch.inference_mode():
            actual = model(torch.zeros(1, 512, 32777))
        self.assertTrue(torch.isfinite(actual).all().item())
        self.assertEqual(torch.count_nonzero(actual).item(), 0)

    def test_onnx_long_reduction_and_dynamic_tail(self):
        original = torch.nn.Sequential(torch.nn.GroupNorm(1, 512)).eval()
        model, _ = accurate_group_norms(original)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'normalization.onnx'
            with torch.inference_mode():
                torch.onnx.export(model, (torch.randn(1, 512, 8999),), str(path),
                    input_names=['x'], output_names=['y'], dynamo=False,
                    dynamic_axes={'x': {0: 'batch', 2: 'frames'},
                                  'y': {0: 'batch', 2: 'frames'}}, opset_version=17)
            graph = onnx.load(path)
            self.assertFalse(any(n.op_type == 'InstanceNormalization' for n in graph.graph.node))
            options = ort.SessionOptions()
            options.intra_op_num_threads = 4
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(str(path), sess_options=options,
                                           providers=['CPUExecutionProvider'])
            for batch, frames in ((1, 8999), (2, 4097), (1, 10003)):
                x = torch.randn(batch, 512, frames)*.03+.1
                with torch.inference_mode():
                    expected = original(x).numpy()
                actual = session.run(['y'], {'x': x.numpy()})[0]
                self.assertEqual(actual.shape, expected.shape)
                relative = np.linalg.norm(actual-expected)/np.linalg.norm(expected)
                self.assertLess(relative, 2e-5)


if __name__ == '__main__':
    unittest.main()
