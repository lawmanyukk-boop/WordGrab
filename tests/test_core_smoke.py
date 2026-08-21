import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch

import app
import audio_pipeline
import engine
import exporters
import job_state
import model_catalog
import store
import system_audio
import transcription_provider


class LiveTranscriptionTests(unittest.TestCase):
    def test_continuous_speech_is_finalized_quickly(self):
        self.assertFalse(engine.should_finalize_live_utterance(0.0, 5.9))
        self.assertTrue(engine.should_finalize_live_utterance(0.0, 6.0))

    def test_natural_pause_finalizes_sentence(self):
        self.assertFalse(engine.should_finalize_live_utterance(0.79, 2.0))
        self.assertTrue(engine.should_finalize_live_utterance(0.8, 2.0))

    def test_streaming_chunk_matches_paraformer_stride(self):
        self.assertEqual(engine.STREAMING_CHUNK_SECONDS, 0.6)

    def test_quiet_speech_is_not_treated_as_silence(self):
        noise_floor = engine.update_live_noise_floor(0.0008, 0.004)
        threshold = engine.live_silence_threshold(noise_floor)
        self.assertLess(threshold, 0.004)

    def test_quiet_live_audio_gets_safe_gain(self):
        import numpy as np
        audio = np.full(9600, 0.004, dtype="float32")
        normalized, gain = engine.normalize_live_audio(audio)
        self.assertGreater(gain, 1.0)
        self.assertLessEqual(float(np.max(np.abs(normalized))), 0.95)

    def test_repeated_fillers_are_coalesced(self):
        self.assertTrue(engine.is_repeated_live_filler([{"text": "嗯"}], "嗯"))
        self.assertFalse(engine.is_repeated_live_filler([{"text": "嗯"}], "嗯，我知道"))
        self.assertTrue(engine.is_live_filler("嗯。"))
        self.assertFalse(engine.is_live_filler("嗯，我知道"))

    def test_live_english_text_keeps_word_spacing(self):
        self.assertEqual(
            engine.accumulate_live_text("hello", "world"),
            "hello world",
        )


class RealtimeAudioPipelineTests(unittest.TestCase):
    def test_streaming_resampler_preserves_duration_across_uneven_blocks(self):
        import numpy as np

        source_rate = 48000
        duration = 1.37
        samples = np.sin(
            2 * np.pi * 440 * np.arange(int(source_rate * duration)) / source_rate
        ).astype("float32") * 0.1
        resampler = audio_pipeline.StreamingResampler(source_rate, 16000)
        outputs = []
        cursor = 0
        for block_size in (317, 509, 997, 241, 1600):
            while cursor < len(samples):
                end = min(len(samples), cursor + block_size)
                outputs.append(resampler.process(samples[cursor:end]))
                cursor = end
                if cursor >= len(samples):
                    break
            if cursor >= len(samples):
                break
        outputs.append(resampler.flush())
        result = np.concatenate(outputs)
        self.assertLessEqual(abs(len(result) - round(len(samples) / 3)), 1)
        self.assertLessEqual(float(np.max(np.abs(result))), 0.101)

    def test_enhancer_raises_quiet_speech_smoothly_without_amplifying_silence(self):
        import numpy as np

        enhancer = audio_pipeline.RealtimeAudioEnhancer(sample_rate=16000)
        quiet = np.sin(2 * np.pi * 220 * np.arange(1600) / 16000).astype("float32") * 0.004
        gains = []
        output = quiet
        for _ in range(20):
            output, metrics = enhancer.process(quiet)
            gains.append(metrics.gain)
        self.assertGreater(gains[-1], gains[0])
        self.assertLessEqual(max(abs(gains[index + 1] - gains[index]) for index in range(len(gains) - 1)), 0.5)
        self.assertGreater(float(np.sqrt(np.mean(np.square(output)))), 0.004)
        silence, silence_metrics = enhancer.process(np.zeros(1600, dtype="float32"))
        self.assertFalse(silence_metrics.speech_active)
        self.assertLess(float(np.max(np.abs(silence))), 0.01)
        self.assertLess(silence_metrics.gain, gains[-1])

    def test_device_resolution_remembers_name_and_reports_fallback(self):
        class FakeDefaults:
            device = (1, 2)

        class FakeSoundDevice:
            default = FakeDefaults()

            @staticmethod
            def query_hostapis():
                return [{"name": "Core Audio"}]

            @staticmethod
            def query_devices():
                return [
                    {"name": "USB Mic", "max_input_channels": 1, "default_samplerate": 48000, "hostapi": 0},
                    {"name": "MacBook Microphone", "max_input_channels": 1, "default_samplerate": 48000, "hostapi": 0},
                    {"name": "Speakers", "max_input_channels": 0, "default_samplerate": 48000, "hostapi": 0},
                ]

        device_id, info, fallback = audio_pipeline.resolve_input_device("USB Mic", FakeSoundDevice)
        self.assertEqual(device_id, 0)
        self.assertEqual(info["name"], "USB Mic")
        self.assertFalse(fallback)
        device_id, info, fallback = audio_pipeline.resolve_input_device("Missing Mic", FakeSoundDevice)
        self.assertEqual(device_id, 1)
        self.assertTrue(fallback)

    def test_bluetooth_device_detection(self):
        self.assertTrue(audio_pipeline.is_bluetooth_device("Ronnie's AirPods Pro"))
        self.assertFalse(audio_pipeline.is_bluetooth_device("MacBook Pro Microphone"))


class TranscriptionProviderTests(unittest.TestCase):
    def test_funasr_provider_declares_streaming_chinese_capabilities(self):
        provider = transcription_provider.get_provider("funasr")
        capabilities = provider.capabilities.as_dict()
        self.assertTrue(capabilities["streaming"])
        self.assertTrue(capabilities["partial_results"])
        self.assertIn("zh", capabilities["languages"])
        self.assertEqual(capabilities["sample_rate"], 16000)

    def test_unknown_provider_is_rejected(self):
        with self.assertRaises(ValueError):
            transcription_provider.get_provider("unfinished-provider")


class DurableJobStateTests(unittest.TestCase):
    def test_job_state_is_atomic_and_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            first = job_state.transition(directory, "recording", "正在录音", item_id="job-a")
            self.assertEqual(first["status"], "recording")
            retried = job_state.mark_retry(directory)
            self.assertEqual(retried["status"], "queued")
            self.assertEqual(retried["retry_count"], 1)
            self.assertFalse((Path(directory) / "job-state.json.tmp").exists())
            saved = job_state.read(directory)
            self.assertEqual(saved["item_id"], "job-a")

    def test_public_job_summary_hides_internal_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            job_state.write(directory, {
                "status": "finalizing",
                "audio_file": "/private/audio.wav",
                "pipeline": {"written_chunks": 10},
            })
            summary = job_state.public_summary(directory)
            self.assertNotIn("audio_file", summary)
            self.assertEqual(summary["pipeline"]["written_chunks"], 10)


class SystemAudioMixTests(unittest.TestCase):
    def test_mix_ducks_system_audio_while_microphone_is_active(self):
        import numpy as np

        microphone = np.full(1600, 0.02, dtype="float32")
        computer = np.full(1600, 0.08, dtype="float32")
        mixed, metrics = system_audio.mix_for_speech(microphone, computer)
        self.assertTrue(metrics["ducking"])
        self.assertLess(metrics["system_gain"], 0.6)
        self.assertLessEqual(float(np.max(np.abs(mixed))), 0.89)

    def test_system_only_mix_keeps_safe_peak(self):
        import numpy as np

        mixed, metrics = system_audio.mix_for_speech(
            np.zeros(800, dtype="float32"), np.ones(800, dtype="float32")
        )
        self.assertFalse(metrics["ducking"])
        self.assertLessEqual(float(np.max(np.abs(mixed))), 0.89)


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

    def test_transcription_progress_tracks_elapsed_time_and_persists_stage_log(self):
        original_log = app.TRANSCRIPTION_LOG
        original_progress = dict(app.PROGRESS)
        try:
            with tempfile.TemporaryDirectory() as directory:
                log_path = Path(directory) / "transcription.log"
                app.TRANSCRIPTION_LOG = str(log_path)
                app.PROGRESS.clear()
                app.PROGRESS["task"] = app._new_transcription_progress("测试录音")
                app.Api._set("task", "正在加载模型…", status="running")
                state = app.PROGRESS["task"]
                self.assertGreaterEqual(state["info"]["elapsed_seconds"], 0)
                record = json.loads(log_path.read_text(encoding="utf-8").strip())
                self.assertEqual(record["item_id"], "task")
                self.assertEqual(record["stage"], "正在加载模型…")
        finally:
            app.TRANSCRIPTION_LOG = original_log
            app.PROGRESS.clear()
            app.PROGRESS.update(original_progress)

    def test_merge_speakers_is_reversible_without_retranscribing(self):
        original_data = {
            "speakers": {"0": "林言", "1": "陈屿"},
            "speaker_colors": {"0": "#111111", "1": "#222222"},
            "segments": [
                {"spk": 0, "start": 0, "text": "甲"},
                {"spk": 1, "start": 1000, "text": "乙"},
                {"spk": 1, "start": 2000, "text": "丙"},
            ],
        }
        current = json.loads(json.dumps(original_data))
        index = [{"id": "merge-test", "n_speakers": 2}]
        with tempfile.TemporaryDirectory() as directory:
            patched = {
                "load_item": lambda _iid: current,
                "save_item": lambda _iid, data: current.update(json.loads(json.dumps(data))),
                "item_dir": lambda _iid: directory,
                "load_index": lambda: index,
                "save_index": lambda items: index.__setitem__(slice(None), items),
                "_write_transcription_log": lambda *_args, **_kwargs: None,
            }
            with patch.multiple(app, **patched):
                result = app.Api().merge_speakers("merge-test", 1, 0)
                self.assertTrue(result["ok"])
                self.assertEqual(result["moved_segments"], 2)
                self.assertEqual(current["speakers"], {"0": "林言"})
                self.assertTrue(all(segment["spk"] == 0 for segment in current["segments"]))

                undone = app.Api().undo_speaker_merge("merge-test")
                self.assertTrue(undone["ok"])
                self.assertEqual(current, original_data)

    def test_delete_speaker_removes_its_segments_preserves_audio_and_can_undo(self):
        original_data = {
            "speakers": {"0": "林言", "1": "背景杂音"},
            "speaker_colors": {"0": "#111111", "1": "#222222"},
            "segments": [
                {"spk": 0, "start": 0, "text": "保留"},
                {"spk": 1, "start": 1000, "text": "杂音一"},
                {"spk": 1, "start": 2000, "text": "杂音二"},
            ],
        }
        current = json.loads(json.dumps(original_data))
        index = [{"id": "delete-speaker-test", "n_speakers": 2}]
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "audio.m4a"
            audio_path.write_bytes(b"original audio")
            summary_path = Path(directory) / "ai_summary.json"
            original_summary = {"result": {"overview": "旧总结"}, "transcript_hash": "before"}
            summary_path.write_text(json.dumps(original_summary), encoding="utf-8")
            patched = {
                "load_item": lambda _iid: current,
                "save_item": lambda _iid, data: current.update(json.loads(json.dumps(data))),
                "item_dir": lambda _iid: directory,
                "load_index": lambda: index,
                "save_index": lambda items: index.__setitem__(slice(None), items),
                "_write_transcription_log": lambda *_args, **_kwargs: None,
            }
            with patch.multiple(app, **patched), patch.dict(app.PROGRESS, {}, clear=True):
                result = app.Api().delete_speaker("delete-speaker-test", 1)
                self.assertTrue(result["ok"])
                self.assertEqual(result["deleted_segments"], 2)
                self.assertEqual(current["speakers"], {"0": "林言"})
                self.assertEqual(current["speaker_colors"], {"0": "#111111"})
                self.assertEqual(current["segments"], [{"spk": 0, "start": 0, "text": "保留"}])
                self.assertEqual(index[0]["n_speakers"], 1)
                self.assertEqual(audio_path.read_bytes(), b"original audio")
                self.assertFalse(summary_path.exists())

                undone = app.Api().undo_speaker_edit("delete-speaker-test")
                self.assertTrue(undone["ok"])
                self.assertEqual(current, original_data)
                self.assertEqual(index[0]["n_speakers"], 2)
                self.assertEqual(audio_path.read_bytes(), b"original audio")
                self.assertEqual(json.loads(summary_path.read_text(encoding="utf-8")), original_summary)

    def test_delete_speaker_rejects_last_speaker_and_active_processing(self):
        single = {
            "speakers": {"0": "林言"},
            "speaker_colors": {"0": "#111111"},
            "segments": [{"spk": 0, "start": 0, "text": "保留"}],
        }
        patched = {
            "load_item": lambda _iid: single,
            "_write_transcription_log": lambda *_args, **_kwargs: None,
        }
        with patch.multiple(app, **patched), patch.dict(app.PROGRESS, {}, clear=True):
            last = app.Api().delete_speaker("single-speaker-test", 0)
            self.assertFalse(last["ok"])
            self.assertIn("至少保留", last["message"])

        with patch.multiple(app, **patched), patch.dict(
            app.PROGRESS,
            {"active-speaker-test": {"status": "running"}},
            clear=True,
        ):
            active = app.Api().delete_speaker("active-speaker-test", 0)
            self.assertFalse(active["ok"])
            self.assertIn("处理", active["message"])

    def test_delete_speaker_rejects_deleting_only_segment_bearing_speaker(self):
        inconsistent = {
            "speakers": {"0": "林言", "1": "未使用标签"},
            "speaker_colors": {"0": "#111111", "1": "#222222"},
            "segments": [{"spk": 0, "start": 0, "text": "唯一内容"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.multiple(
                app,
                load_item=lambda _iid: inconsistent,
                save_item=lambda *_args, **_kwargs: None,
                item_dir=lambda _iid: directory,
                load_index=lambda: [],
                save_index=lambda _items: None,
                _write_transcription_log=lambda *_args, **_kwargs: None,
            ), patch.dict(app.PROGRESS, {}, clear=True):
                result = app.Api().delete_speaker("inconsistent-speaker-test", 0)
        self.assertFalse(result["ok"])
        self.assertIn("至少保留", result["message"])

    def test_delete_speaker_rolls_back_if_index_save_fails(self):
        original_data = {
            "speakers": {"0": "林言", "1": "背景杂音"},
            "speaker_colors": {"0": "#111111", "1": "#222222"},
            "segments": [
                {"spk": 0, "start": 0, "text": "保留"},
                {"spk": 1, "start": 1000, "text": "杂音"},
            ],
        }
        current = json.loads(json.dumps(original_data))
        index = [{"id": "rollback-test", "n_speakers": 2}]
        save_index_calls = 0

        def save_index_with_first_failure(items):
            nonlocal save_index_calls
            save_index_calls += 1
            if save_index_calls == 1:
                raise OSError("index write failed")
            index[:] = json.loads(json.dumps(items))

        def save_current(_iid, data):
            saved = json.loads(json.dumps(data))
            current.clear()
            current.update(saved)

        with tempfile.TemporaryDirectory() as directory:
            backup_path = Path(directory) / "speaker_merge_backup.json"
            previous_backup = {"operation": "merge", "data": {"previous": True}}
            backup_path.write_text(json.dumps(previous_backup), encoding="utf-8")
            patched = {
                "load_item": lambda _iid: current,
                "save_item": save_current,
                "item_dir": lambda _iid: directory,
                "load_index": lambda: index,
                "save_index": save_index_with_first_failure,
                "_write_transcription_log": lambda *_args, **_kwargs: None,
            }
            with patch.multiple(app, **patched), patch.dict(app.PROGRESS, {}, clear=True):
                result = app.Api().delete_speaker("rollback-test", 1)

            self.assertFalse(result["ok"])
            self.assertEqual(current, original_data)
            self.assertEqual(index[0]["n_speakers"], 2)
            self.assertEqual(json.loads(backup_path.read_text(encoding="utf-8")), previous_backup)

    def test_undo_speaker_delete_rejects_new_transcript_edits(self):
        current = {
            "speakers": {"0": "林言", "1": "背景杂音"},
            "speaker_colors": {"0": "#111111", "1": "#222222"},
            "segments": [
                {"spk": 0, "start": 0, "text": "保留"},
                {"spk": 1, "start": 1000, "text": "杂音"},
            ],
        }
        index = [{"id": "stale-undo-test", "n_speakers": 2}]
        with tempfile.TemporaryDirectory() as directory:
            def save_current(_iid, data):
                saved = json.loads(json.dumps(data))
                current.clear()
                current.update(saved)

            patched = {
                "load_item": lambda _iid: current,
                "save_item": save_current,
                "item_dir": lambda _iid: directory,
                "load_index": lambda: index,
                "save_index": lambda items: index.__setitem__(slice(None), json.loads(json.dumps(items))),
                "_write_transcription_log": lambda *_args, **_kwargs: None,
            }
            with patch.multiple(app, **patched), patch.dict(app.PROGRESS, {}, clear=True):
                deleted = app.Api().delete_speaker("stale-undo-test", 1)
                self.assertTrue(deleted["ok"])
                current["segments"][0]["text"] = "删除后又编辑过"
                undone = app.Api().undo_speaker_edit("stale-undo-test")

            self.assertFalse(undone["ok"])
            self.assertIn("新的修改", undone["message"])
            self.assertEqual(current["segments"][0]["text"], "删除后又编辑过")


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
