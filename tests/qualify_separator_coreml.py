"""Explicit native-provider regression: python -m unittest tests.qualify_separator_coreml.

Requires ONNX Runtime with Core ML on macOS. Not a model-free CI contract;
missing provider is a failure, never a skip or CPU fallback pass.
"""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import onnxruntime as ort

from scripts.export_mossformer2_separator import explicit_last_index_gathers
from tests.test_separator_gather import graph


class CoreMLLastIndexTests(unittest.TestCase):
    def test_dynamic_last_index_and_actual_provider(self):
        self.assertIn('CoreMLExecutionProvider', ort.get_available_providers())
        model = graph([-1], input_shape=('length',))
        self.assertEqual(explicit_last_index_gathers(model), 1)
        with tempfile.TemporaryDirectory() as folder:
            options = ort.SessionOptions()
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
            options.enable_profiling = True
            options.profile_file_prefix = str(Path(folder)/'profile')
            session = ort.InferenceSession(model.SerializeToString(), sess_options=options,
                providers=[('CoreMLExecutionProvider', {'ModelFormat': 'MLProgram',
                    'MLComputeUnits': 'CPUAndGPU', 'RequireStaticInputShapes': '0'})])
            for values in ([7], [1, 3999, 512], [1, 8999, 1024], [2, 3, 4, 5, 9]):
                actual = session.run(None, {'x': np.array(values, dtype=np.float32)})[0]
                np.testing.assert_array_equal(actual, np.array([values[-1]], dtype=np.float32))
            events = json.loads(Path(session.end_profiling()).read_text())
            count = sum(e.get('args', {}).get('provider') == 'CoreMLExecutionProvider' for e in events)
            self.assertGreaterEqual(count, 4, 'every call must execute through Core ML')


if __name__ == '__main__':
    unittest.main()
