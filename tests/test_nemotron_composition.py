"""Model-free checks for the recorded Nemotron acceptance tool, not quality."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from copy import deepcopy

from scripts.prove_nemotron_composition import attribution_evidence, bindings, cuda_placement, distance, words
from scripts.prove_speaker_registry_audio import acceptance


class CompositionToolTests(unittest.TestCase):
    def test_registry_withholding_is_not_successful_identification(self):
        result = dict(cases=[dict(passed=True)], association_after_process_restart=True,
                      complete_profile_coverage=True)
        self.assertTrue(acceptance(result))
        for changes in (dict(cases=[]), dict(cases=[dict(passed=False)]),
                        dict(complete_profile_coverage=False),
                        dict(association_after_process_restart=False)):
            self.assertFalse(acceptance({**result, **changes}))

    def test_attribution_uses_coverage_not_raw_clean_islands(self):
        data = dict(attribution_policy="whole-final-coverage-v1", samples=64000,
                    tracks=[[1]], selected_evidence=[dict(track=0,samples=40000)],
                    attribution_evidence=[dict(track=0,samples=0,regions=[],
                        reason="speaker_track_coverage_unverified",overlap_samples=0,
                        competing_uncertain_samples=160)])
        self.assertEqual(attribution_evidence(data)[0]["samples"], 0)
        good = deepcopy(data)
        good["attribution_evidence"][0].update(samples=32000, reason="",
            competing_uncertain_samples=0, regions=[[1000,17000],[20000,36000]])
        self.assertEqual(attribution_evidence(good)[0]["samples"], 32000)
        for changes in (dict(samples=40000), dict(reason=""), dict(track=1),
                        dict(samples=True), dict(regions=[[0,1]]),
                        dict(overlap_samples=-1), dict(competing_uncertain_samples=None)):
            bad = deepcopy(data)
            bad["attribution_evidence"][0].update(changes)
            with self.assertRaises(ValueError):
                attribution_evidence(bad)
        for changes in (dict(overlap_samples=160), dict(competing_uncertain_samples=160),
                        dict(regions=[[1000,17000],[16000,32000]]),
                        dict(regions=[[1000,17000],[60000,76000]])):
            bad = deepcopy(good)
            bad["attribution_evidence"][0].update(changes)
            with self.assertRaises(ValueError):
                attribution_evidence(bad)
        for key in ("attribution_policy", "attribution_evidence"):
            bad = deepcopy(data)
            del bad[key]
            with self.assertRaises(ValueError):
                attribution_evidence(bad)

    def test_cuda_requires_neural_compute_not_registration_or_copies(self):
        events = [{"args": {"provider": "CUDAExecutionProvider", "op_name": op}}
                  for op in ("MatMul", "Conv", "LayerNormalization", "Softmax")]
        shape = {"args": {"provider": "CPUExecutionProvider", "op_name": "Gather",
                          "input_type_shape": [{"int64": [3]}],
                          "output_type_shape": [{"int64": []}]}}
        self.assertEqual(len(cuda_placement(events + [shape])), 5)
        for bad in ([], events[:-1], [{"args": {"provider": "CUDAExecutionProvider",
                                               "op_name": "MemcpyFromHost"}}]):
            with self.assertRaises(ValueError):
                cuda_placement(bad)
        for change in ({"op_name": "MatMul"}, {"output_type_shape": [{"float": [3]}]},
                       {"input_type_shape": []}, {"provider": "UnknownProvider"}):
            with self.assertRaises(ValueError):
                cuda_placement(events + [{"args": {**shape["args"], **change}}])

    def test_word_error_counts_insert_delete_substitute(self):
        self.assertEqual(distance(words("one two"), words("ONE, two!")), 0)
        self.assertEqual(distance(words("one two"), words("two")), 1)
        self.assertEqual(distance(words("one two"), words("one three")), 1)
        self.assertEqual(distance([], words("inserted words")), 2)

    def test_bindings_include_external_weights_and_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = SimpleNamespace(probe=root / "probe", model=root / "model",
                                   panel=root / "panel", ort_library=root / "ort",
                                   graphs=root / "graphs", native_root=root / "native")
            for path in (args.probe, args.model, args.panel, args.ort_library):
                path.write_bytes(b"fixture")
            args.graphs.mkdir()
            for name in ("tokens.json", "mel.f32"):
                (args.graphs / name).write_bytes(b"fixture")
            for name in ("asr_preencode", "asr_encoder", "asr_decoder", "asr_joiner"):
                directory = args.graphs / name
                directory.mkdir()
                (directory / "model.onnx").write_bytes(b"fixture")
            weights = args.graphs / "asr_encoder" / "weights-000.bin"
            weights.write_bytes(b"before")
            libraries = args.native_root / "lib"
            libraries.mkdir(parents=True)
            (libraries / "libnemo_speech_asr_c.so").write_bytes(b"fixture")
            before = bindings(args)
            weights.write_bytes(b"after")
            self.assertNotEqual(before, bindings(args))
            self.assertIn("ort_library", before)
            self.assertIn("native/libnemo_speech_asr_c.so", before)
            self.assertNotIn(str(root), str(before))
            # Windows packages intentionally place DLLs in bin, not lib.
            windows = args.native_root / "bin"
            windows.mkdir()
            dll = windows / "nemo_speech_asr_c.dll"
            dll.write_bytes(b"windows-before")
            args.native_library_subdir = "bin"
            win_bound = bindings(args)
            self.assertIn("native/nemo_speech_asr_c.dll", win_bound)
            self.assertNotIn("native/libnemo_speech_asr_c.so", win_bound)
            dll.write_bytes(b"windows-after")
            self.assertNotEqual(win_bound, bindings(args))
            dll.unlink()
            with self.assertRaisesRegex(ValueError, "native library inventory missing"):
                bindings(args)
            args.native_library_subdir = "lib"
            cuda = root / "cuda"
            cuda.mkdir()
            (cuda / "libcudnn.so").write_bytes(b"before")
            args.encoder_runtime = [cuda]
            bound = bindings(args)
            (cuda / "libcudnn.so").write_bytes(b"after")
            self.assertNotEqual(bound, bindings(args))
            (args.graphs / "asr_decoder" / "model.onnx").unlink()
            with self.assertRaisesRegex(ValueError, "missing ASR graph"):
                bindings(args)


if __name__ == "__main__":
    unittest.main()
