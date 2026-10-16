import tempfile
from pathlib import Path
import unittest

import onnx
from onnx import TensorProto, helper
from scripts.compact_multitalker_graphs import repack_graph


class GraphPacking(unittest.TestCase):
    def source(self, root):
        source = root / 'source'
        source.mkdir()
        (source / 'data').write_bytes(b'prefix' + bytes(range(24)) + b'trailer')
        values = []
        for i in range(3):
            tensor = TensorProto(name='tensor'+str(i), data_type=TensorProto.FLOAT, dims=[2],
                                 data_location=TensorProto.EXTERNAL)
            for key, value in [('location', 'data'), ('offset', str(6+8*i)), ('length', '8')]:
                item = tensor.external_data.add()
                item.key, item.value = key, value
            values.append(tensor)
        model = helper.make_model(helper.make_graph([], 'packing', [], [], values))
        onnx.save_model(model, source / 'model.onnx')
        return source

    def test_weights_unchanged_across_bounded_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.source(root)
            original = (source / 'model.onnx').read_bytes()
            proof = repack_graph(source, root / 'packed', 16)
            self.assertEqual(proof['tensor_count'], 3)
            self.assertEqual(proof['weight_files'], 2)
            self.assertEqual((root/'packed/weights-000.bin').read_bytes(), bytes(range(16)))
            self.assertEqual((root/'packed/weights-001.bin').read_bytes(), bytes(range(16,24)))
            self.assertEqual((source/'model.onnx').read_bytes(), original)

    def test_escaping_source_and_oversized_tensor_refused(self):
        for escape in (False, True):
            with self.subTest(escape=escape), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = self.source(root)
                if escape:
                    model = onnx.load(source/'model.onnx', load_external_data=False)
                    model.graph.initializer[0].external_data[0].value = '../data'
                    onnx.save_model(model, source/'model.onnx')
                with self.assertRaises(ValueError):
                    repack_graph(source, root/'packed', 16 if escape else 4)


if __name__ == '__main__':
    unittest.main()
