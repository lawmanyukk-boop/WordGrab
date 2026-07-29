import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import app
import engine
import exporters
import model_catalog
import store


class LiveTranscriptionTests(unittest.TestCase):
    def test_continuous_speech_is_finalized_quickly(self):
        self.assertFalse(engine.should_finalize_live_utterance(0.0, 4.7))
        self.assertTrue(engine.should_finalize_live_utterance(0.0, 4.8))

    def test_natural_pause_finalizes_sentence(self):
        self.assertFalse(engine.should_finalize_live_utterance(0.64, 2.0))
        self.assertTrue(engine.should_finalize_live_utterance(0.65, 2.0))

    def test_live_english_text_keeps_word_spacing(self):
        self.assertEqual(
            engine.accumulate_live_text("hello", "world"),
            "hello world",
        )


class StorageAndExportTests(unittest.TestCase):
    def test_speaker_colors_are_stable(self):
        first = store.make_speaker_colors("meeting-a", [0, 1, 2])
        second = store.make_speaker_colors("meeting-a", [0, 1, 2])
        self.assertEqual(first, second)
        self.assertEqual(len(set(first.values())), 3)

    def test_export_rows_keep_timestamps_and_speaker(self):
        rows = exporters.build_export_rows(
            [{"spk": 0, "start": 1000, "end": 3000, "text": "测试内容"}],
            {"0": "说话人1"},
            4,
        )
        self.assertEqual(rows[0]["speaker"], "说话人1")
        self.assertEqual(rows[0]["start_time"], "00:00:01")
        self.assertEqual(rows[0]["end_time"], "00:00:03")


class ApplicationStartupTests(unittest.TestCase):
    def test_required_runtime_functions_are_imported(self):
        self.assertTrue(callable(app.make_speaker_colors))
        self.assertTrue(callable(app.recover_interrupted_recordings))

    def test_open_item_missing_transcript_returns_readable_error(self):
        original_load_item = app.load_item
        original_audio_path_of = app.audio_path_of
        try:
            app.load_item = lambda _iid: (_ for _ in ()).throw(FileNotFoundError())
            app.audio_path_of = lambda _iid: None
            result = app.Api().open_item("missing")
        finally:
            app.load_item = original_load_item
            app.audio_path_of = original_audio_path_of
        self.assertFalse(result["ok"])
        self.assertIn("不完整", result["message"])

    def test_live_prepare_never_downloads_before_models_are_ready(self):
        original = model_catalog.all_models_ready
        try:
            model_catalog.all_models_ready = lambda: False
            result = app.Api().prepare_live_transcription()
        finally:
            model_catalog.all_models_ready = original
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "MODEL_NOT_READY")


class PackagedRuntimeTests(unittest.TestCase):
    def test_required_funasr_registry_components_exist(self):
        result = engine.verify_runtime_components()
        self.assertEqual(result["funasr"], "1.1.14")
        self.assertIn("SeacoParaformer", result["components"]["model_classes"])

    def test_model_validation_requires_revision_and_exact_file_sizes(self):
        spec = model_catalog.ModelSpec(
            key="test",
            label="测试模型",
            model_id="iic/test-model",
            revision="v1",
            expected_bytes=7,
            required_files=(("model.bin", 4),),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / ".mv").write_text("Revision:v1", encoding="utf-8")
            (path / "model.bin").write_bytes(b"1234")
            ready, problems = model_catalog.validate_model_directory(path, spec)
            self.assertTrue(ready, problems)
            (path / "model.bin").write_bytes(b"bad")
            ready, problems = model_catalog.validate_model_directory(path, spec)
            self.assertFalse(ready)
            self.assertIn("model.bin 大小异常", problems)

    def test_download_progress_includes_modelscope_temporary_chunks(self):
        spec = model_catalog.MODEL_SPECS[0]
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                "os.environ",
                {
                    "MODELSCOPE_CACHE": directory,
                    "WORDGRAB_DISABLE_LEGACY_MODEL_CACHE": "1",
                },
            ):
                candidates = model_catalog._candidate_directories(spec)
        self.assertTrue(
            any("._____temp" in path.parts for path in candidates),
            candidates,
        )


if __name__ == "__main__":
    unittest.main()
