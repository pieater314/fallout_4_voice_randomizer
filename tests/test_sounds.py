import json
import os
import struct
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from test_ba2 import archive_bytes
from voice_randomizer.audio import AudioConversionError
from voice_randomizer.pipeline import Cancelled, build
from voice_randomizer.sounds import SoundFormat, UnsupportedSound, convert_sound, sound_format


def audio(kind, payload=b"12", loop=None, cue=False):
    codec = 1 if kind == "wav" else 0x161
    header = struct.pack("<HHIIHH", codec, 1, 44100, 88200, 2, 16)

    def chunk(name, data):
        return name + struct.pack("<I", len(data)) + data + (b"\0" if len(data) % 2 else b"")

    contents = chunk(b"fmt ", header) + chunk(b"data", payload)
    if loop is not None:
        sample = struct.pack("<9I", 0, 0, 22676, 60, 0, 0, 0, 1, 0)
        sample += struct.pack("<6I", 0, 0, loop[0], loop[1], 0, 0)
        contents += chunk(b"smpl", sample)
    if cue:
        contents += chunk(b"cue ", struct.pack("<I", 0))
    return b"RIFF" + struct.pack("<I", len(contents) + 4) + (
        b"WAVE" if kind == "wav" else b"XWMA"
    ) + contents


class SoundModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / "game"
        self.data = self.game / "Data"
        self.data.mkdir(parents=True)
        (self.game / "Fallout4.exe").touch()
        self.work = self.root / "work"
        self.entries = [
            (b"sound/fx/a.wav", audio("wav", b"AA"), False),
            (b"sound/fx/b.xwm", audio("xwm", b"BB"), True),
            (b"sound/fx/dup.xwm", audio("xwm"), False),
            (b"sound/fx/cue.wav", audio("wav", cue=True), False),
            (b"sound/voice/game.esm/npc/a.fuz", b"voice", False),
            (b"music/song.xwm", audio("xwm"), False),
        ]
        (self.data / "Fallout4 - Sounds.ba2").write_bytes(archive_bytes(1, self.entries))
        startup = [
            (b"sound/fx/dup.xwm", audio("xwm"), False),
            (b"sound/fx/start.xwm", audio("xwm"), False),
        ]
        (self.data / "Fallout4 - Startup.ba2").write_bytes(archive_bytes(8, startup))

    def run_build(self, **kwargs):
        return build(self.game, self.work, "loose", "seed", lambda *_: None,
                     lambda: False, mode="sounds", **kwargs)

    def test_cross_format_shuffle_and_exclusions(self):
        calls = []

        def convert(data, source, destination, directory, check_cancel):
            check_cancel()
            calls.append((source.kind, destination.kind))
            return audio(destination.kind, data[-2:])

        with patch("voice_randomizer.pipeline.convert_sound", side_effect=convert):
            out = self.run_build(create_zip=True)
        self.assertCountEqual(calls, [("xwm", "wav"), ("wav", "xwm")])
        report = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(report["mode"], "sounds")
        self.assertEqual(len(report["mapping"]), 2)
        self.assertEqual(len(report["exclusions"]), 3)
        self.assertEqual(
            {item["reason"] for item in report["exclusions"]},
            {"Duplicate path", "Unsupported audio cue or loop metadata",
             "Startup loose-file override is not verified"},
        )
        self.assertEqual(
            {item["destination_format"] for item in report["mapping"]}, {"wav", "xwm"}
        )
        with zipfile.ZipFile(out / "Fallout4VoiceRandomizer.zip") as archive:
            self.assertEqual(set(archive.namelist()), {"sound/fx/a.wav", "sound/fx/b.xwm"})
            for name in archive.namelist():
                self.assertEqual(sound_format(archive.read(name)).kind, Path(name).suffix[1:])
        log = (out / "randomization_log.txt").read_text(encoding="utf-8")
        self.assertIn("Excluded sound paths: 3", log)

    def test_converter_failure_does_not_publish(self):
        with patch("voice_randomizer.pipeline.convert_sound", side_effect=ValueError("conversion failed")):
            with self.assertRaisesRegex(ValueError, "Cannot replace .*conversion failed"):
                self.run_build()
        self.assertFalse((self.work / "Fallout4VoiceRandomizer").exists())

    def test_music_paths_are_excluded_from_both_sides_of_the_mapping(self):
        music_paths = [
            "sound/fx/MUS/Radio/track.xwm",
            "sound/fx/DLC04/MuS/RobotGreeter/effect.xwm",
            "sound/fx/UI/Background_MuSiC.wav",
            "sound/fx/DLC04_MUS_Robot.xwm",
            "sound/fx/AMB_Jingle_Roomtone.wav",
        ]
        entries = self.entries + [
            (path.encode(), b"not audio", False) for path in music_paths
        ] + [(b"sound/fx/museum.wav", audio("wav"), False)]
        (self.data / "Fallout4 - Sounds.ba2").write_bytes(archive_bytes(1, entries))

        with patch("voice_randomizer.pipeline.convert_sound",
                   side_effect=lambda data, source, destination, directory, check_cancel:
                   audio(destination.kind)):
            out = self.run_build()

        report = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        exclusions = {item["path"]: item["reason"] for item in report["exclusions"]}
        for path in music_paths:
            self.assertEqual(exclusions[path], "Music-related path")
        mapped = {item[side] for item in report["mapping"] for side in ("source", "destination")}
        self.assertTrue(mapped.isdisjoint(music_paths))
        self.assertIn("sound/fx/museum.wav", mapped)
        log = (out / "randomization_log.txt").read_text(encoding="utf-8")
        self.assertIn("Music-related path", log)

    def test_seed_repeats_mapping(self):
        def convert(data, source, destination, directory, check_cancel):
            return audio(destination.kind)

        with patch("voice_randomizer.pipeline.convert_sound", side_effect=convert):
            first = self.run_build()
            with patch("voice_randomizer.pipeline._SOUND_WORKERS", 1):
                second = build(self.game, self.root / "other", "loose", "seed",
                               lambda *_: None, lambda: False, mode="sounds")
        reports = [
            json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            for output in (first, second)
        ]
        self.assertEqual(reports[0]["mapping"], reports[1]["mapping"])
        for item in reports[0]["mapping"]:
            destination = item["destination"]
            self.assertEqual((first / "mod" / destination).read_bytes(),
                             (second / "mod" / destination).read_bytes())

    def test_runs_four_conversions_at_once(self):
        entries = [
            (f"sound/fx/{index}.wav".encode(), audio("wav", bytes([index, 0])), False)
            for index in range(8)
        ]
        (self.data / "Fallout4 - Sounds.ba2").write_bytes(archive_bytes(1, entries))
        barrier = threading.Barrier(4, timeout=5)
        lock = threading.Lock()
        active = 0
        peak = 0
        started = 0
        progress = []

        def convert(data, source, destination, directory, check_cancel):
            nonlocal active, peak, started
            with lock:
                active += 1
                started += 1
                peak = max(peak, active)
                first_batch = started <= 4
            try:
                if first_batch:
                    barrier.wait()
                check_cancel()
                return data
            finally:
                with lock:
                    active -= 1

        with patch("voice_randomizer.pipeline.convert_sound", side_effect=convert):
            out = build(self.game, self.work, "loose", "seed",
                        lambda *event: progress.append(event), lambda: False, mode="sounds")
        self.assertEqual(peak, 4)
        self.assertEqual(started, 8)
        self.assertIn(("Processing sounds", 8, 8), progress)
        self.assertEqual(len(json.loads((out / "manifest.json").read_text())["mapping"]), 8)

    def test_worker_failure_cancels_other_conversions(self):
        entries = [
            (f"sound/fx/{index}.wav".encode(), audio("wav"), False)
            for index in range(4)
        ]
        (self.data / "Fallout4 - Sounds.ba2").write_bytes(archive_bytes(1, entries))
        barrier = threading.Barrier(4, timeout=5)
        lock = threading.Lock()
        started = 0
        cancelled_workers = []

        def convert(data, source, destination, directory, check_cancel):
            nonlocal started
            with lock:
                started += 1
                number = started
            barrier.wait()
            if number == 1:
                raise ValueError("conversion failed")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    check_cancel()
                except Cancelled:
                    with lock:
                        cancelled_workers.append(number)
                    raise
                time.sleep(0.01)
            self.fail("Worker did not receive cancellation.")

        with patch("voice_randomizer.pipeline.convert_sound", side_effect=convert):
            with self.assertRaisesRegex(ValueError, "conversion failed"):
                self.run_build()
        self.assertEqual(len(cancelled_workers), 3)
        self.assertFalse((self.work / "Fallout4VoiceRandomizer").exists())

    def test_user_cancellation_stops_active_workers(self):
        entries = [
            (f"sound/fx/{index}.wav".encode(), audio("wav"), False)
            for index in range(4)
        ]
        (self.data / "Fallout4 - Sounds.ba2").write_bytes(archive_bytes(1, entries))
        barrier = threading.Barrier(4, timeout=5)
        cancelled = threading.Event()
        lock = threading.Lock()
        stopped = []

        def convert(data, source, destination, directory, check_cancel):
            barrier.wait()
            cancelled.set()
            try:
                check_cancel()
            except Cancelled:
                with lock:
                    stopped.append(True)
                raise
            self.fail("Worker did not receive cancellation.")

        with patch("voice_randomizer.pipeline.convert_sound", side_effect=convert):
            with self.assertRaises(Cancelled):
                build(self.game, self.work, "loose", "seed", lambda *_: None,
                      cancelled.is_set, mode="sounds")
        self.assertEqual(len(stopped), 4)
        self.assertFalse((self.work / "Fallout4VoiceRandomizer").exists())
        self.assertEqual(list(self.work.iterdir()), [])

    def test_incomplete_dlc_and_cancellation_stop(self):
        (self.data / "DLCRobot.esm").touch()
        with self.assertRaisesRegex(ValueError, "Incomplete DLC"):
            self.run_build()
        (self.data / "DLCRobot.esm").unlink()
        with self.assertRaises(Cancelled):
            build(self.game, self.work, "loose", "seed", lambda *_: None,
                  lambda: True, mode="sounds")
        self.assertFalse((self.work / "Fallout4VoiceRandomizer").exists())

    def test_bad_audio_and_unsafe_paths_are_rejected(self):
        for name, content, message in (
            (b"sound/fx/bad.wav", b"bad", "Invalid sound"),
            (b"sound/fx/../bad.wav", audio("wav"), "unsafe sound path"),
        ):
            with self.subTest(name=name):
                (self.data / "Fallout4 - Sounds.ba2").write_bytes(
                    archive_bytes(1, [(name, content, False)])
                )
                with self.assertRaisesRegex(ValueError, message):
                    self.run_build()
                self.assertFalse((self.work / "Fallout4VoiceRandomizer").exists())

    def test_detects_invalid_and_cue_audio(self):
        with self.assertRaises(UnsupportedSound):
            sound_format(audio("wav", cue=True))
        with self.assertRaises(UnsupportedSound):
            sound_format(audio("wav", b"A" * 4, loop=(0, 1), cue=True))
        with self.assertRaises(ValueError):
            sound_format(audio("wav")[:-1])

    def test_loop_points_scale_for_destination_and_source_loops_are_removed(self):
        source = audio("wav", b"A" * 20)
        destination = audio("wav", b"B" * 40, loop=(5, 14))
        source_format = sound_format(source)
        destination_format = sound_format(destination)

        looped = convert_sound(source, source_format, destination_format,
                               self.root, lambda: None)
        loop = sound_format(looped).loop
        self.assertIsNotNone(loop)
        self.assertEqual(loop.frames, 10)
        self.assertEqual(struct.unpack_from("<II", loop.chunk, 44), (2, 7))
        self.assertIn(b"A" * 20, looped)

        unlooped = convert_sound(destination, destination_format, source_format,
                                 self.root, lambda: None)
        self.assertEqual(unlooped, audio("wav", b"B" * 40))
        self.assertIsNone(sound_format(unlooped).loop)

        other_loop = audio("wav", b"C" * 12, loop=(1, 4))
        replaced = convert_sound(other_loop, sound_format(other_loop), destination_format,
                                 self.root, lambda: None)
        replacement_loop = sound_format(replaced).loop
        self.assertIsNotNone(replacement_loop)
        self.assertEqual(struct.unpack_from("<II", replacement_loop.chunk, 44), (1, 4))

        one_frame = audio("wav", b"AA")
        shortest = convert_sound(one_frame, sound_format(one_frame), destination_format,
                                 self.root, lambda: None)
        shortest_loop = sound_format(shortest).loop
        self.assertIsNotNone(shortest_loop)
        self.assertEqual(struct.unpack_from("<II", shortest_loop.chunk, 44), (0, 0))

    def test_loop_metadata_rejects_bad_points_and_empty_replacement(self):
        with self.assertRaisesRegex(ValueError, "outside the audio data"):
            sound_format(audio("wav", b"A" * 4, loop=(0, 2)))
        incomplete = bytearray(audio("wav", b"A" * 4, loop=(0, 1)))
        struct.pack_into("<I", incomplete, incomplete.index(b"smpl") + 36, 2)
        with self.assertRaisesRegex(ValueError, "Incomplete sample loop"):
            sound_format(bytes(incomplete))

        source = audio("wav", b"")
        destination = audio("wav", b"B" * 4, loop=(0, 1))
        with self.assertRaisesRegex(AudioConversionError, "empty audio"):
            convert_sound(source, sound_format(source), sound_format(destination),
                          self.root, lambda: None)

    def test_build_preserves_destination_loop_after_both_wav_swaps(self):
        plain = audio("wav", b"A" * 20)
        looped = audio("wav", b"B" * 40, loop=(5, 14))
        entries = [
            (b"sound/fx/plain.wav", plain, False),
            (b"sound/fx/loop.wav", looped, False),
        ]
        (self.data / "Fallout4 - Sounds.ba2").write_bytes(archive_bytes(1, entries))
        (self.data / "Fallout4 - Startup.ba2").unlink()
        out = self.run_build()
        result_loop = (out / "mod" / "sound" / "fx" / "loop.wav").read_bytes()
        result_plain = (out / "mod" / "sound" / "fx" / "plain.wav").read_bytes()
        loop = sound_format(result_loop).loop
        self.assertIsNotNone(loop)
        self.assertEqual(struct.unpack_from("<II", loop.chunk, 44), (2, 7))
        self.assertEqual(result_plain, audio("wav", b"B" * 40))

    @unittest.skipUnless(os.name == "nt", "The bundled encoder requires Windows")
    def test_real_converter_handles_both_formats_and_stereo_48000(self):
        source = audio("wav", b"\0\0" * 4410, loop=(441, 3527))
        wav = sound_format(source)
        stereo = SoundFormat("xwm", 2, 48000, 16)
        converted = convert_sound(source, wav, stereo, self.root, lambda: None)
        self.assertEqual(sound_format(converted), stereo)
        self.assertIsNone(sound_format(converted).loop)
        restored = convert_sound(converted, stereo, wav, self.root, lambda: None)
        self.assertEqual(sound_format(restored), wav)
        loop = sound_format(restored).loop
        self.assertIsNotNone(loop)
        start, end = struct.unpack_from("<II", loop.chunk, 44)
        self.assertEqual(start, 441 * loop.frames // 4410)
        self.assertEqual(end, (3528 * loop.frames + 4409) // 4410 - 1)

    def test_converter_cancellation_stops_without_output(self):
        source = audio("wav")
        target = SoundFormat("xwm", 1, 44100, 16)

        def cancel():
            raise Cancelled()

        with self.assertRaises(Cancelled):
            convert_sound(source, sound_format(source), target, self.root, cancel)
        self.assertEqual(list(self.root.iterdir()), [self.game])

    def test_rejects_invalid_converter_output(self):
        source = audio("xwm")

        def invalid(command, check_cancel):
            Path(command[-1]).write_bytes(b"not audio")

        with patch("voice_randomizer.sounds._run", side_effect=invalid):
            with self.assertRaisesRegex(AudioConversionError, "Invalid converted audio"):
                convert_sound(source, sound_format(source), SoundFormat("wav", 1, 44100, 16),
                              self.root, lambda: None)


if __name__ == "__main__":
    unittest.main()
