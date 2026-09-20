import unittest
import json
import tempfile
from pathlib import Path
import numpy as np

from scripts.export_speaker_aware_graphs import compare
from scripts.prove_native_multitalker import verify_graphs
from scripts.run_speaker_aware_reference import digest, NEMO_REV


class GraphParity(unittest.TestCase):
    def test_values_geometry_and_count(self):
        expected=[np.zeros((1,3,4),np.float32),np.array([3],np.int64)]
        self.assertEqual(compare(expected,expected,'exact'),[0.,0.])
        with self.assertRaisesRegex(AssertionError,'census'):
            compare(expected[:-1],expected,'missing')
        with self.assertRaisesRegex(AssertionError,'shape/dtype'):
            compare([np.zeros((1,1,4),np.float32),expected[1]],expected,'broadcast')
        with self.assertRaisesRegex(AssertionError,'shape/dtype'):
            compare([expected[0].astype(np.float64),expected[1]],expected,'precision')

    def test_integer_clock_is_exact(self):
        with self.assertRaises(AssertionError):
            compare([np.array([100001],np.int64)],[np.array([100000],np.int64)],'clock')

    def test_nonfinite_is_never_equal(self):
        for number in (np.nan,np.inf,-np.inf):
            with self.subTest(number=number),self.assertRaisesRegex(AssertionError,'nonfinite'):
                compare([np.array([number],np.float32)],[np.array([number],np.float32)],'finite')

    def test_failure_names_graph_case_and_output(self):
        with self.assertRaisesRegex(AssertionError,'encoder/reentry/output-0'):
            compare([np.array([.001],np.float32)],[np.array([0.],np.float32)],'encoder/reentry')


class GraphBindings(unittest.TestCase):
    def fixture(self,root):
        artifact=root/'fixture.data';artifact.write_bytes(b'graph-fixture')
        result=dict(passed=True,upstream_revision=NEMO_REV,ort_optimization='disabled',blank_equals_start=True,
            artifacts={'fixture.data':dict(sha256=digest(artifact),bytes=artifact.stat().st_size)},
            graphs=[dict(name=name,passed=True,cases=[dict(passed=True)],multiple_captures_refused=True)
                    for name in ('asr_preencode','diar_preencode','asr_encoder','asr_decoder','asr_joiner','diar_classifier')])
        return result

    def save(self,root,value):
        (root/'result.json').write_text(json.dumps(value))

    def test_exact_inventory_and_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);value=self.fixture(root);self.save(root,value)
            self.assertTrue(verify_graphs(root)['passed'])
            (root/'unexpected.data').write_bytes(b'extra')
            with self.assertRaisesRegex(ValueError,'census'):verify_graphs(root)
            (root/'unexpected.data').unlink()
            (root/'fixture.data').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'binding'):verify_graphs(root)

    def test_failed_synthetic_case_cannot_be_promoted(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);value=self.fixture(root)
            value['graphs'][0]['cases'][0]['passed']=False;self.save(root,value)
            with self.assertRaisesRegex(ValueError,'failed graph case'):verify_graphs(root)

    def test_duplicate_missing_graph_and_unproven_start_are_refused(self):
        for defect in ('duplicate','missing','blank','capture'):
            with self.subTest(defect=defect),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);value=self.fixture(root)
                if defect=='duplicate':value['graphs'].append(value['graphs'][0])
                elif defect=='missing':value['graphs'].pop()
                elif defect=='blank':value['blank_equals_start']=False
                else:value['graphs'][0]['multiple_captures_refused']=False
                self.save(root,value)
                with self.assertRaises(ValueError):verify_graphs(root)


if __name__=='__main__':unittest.main()
