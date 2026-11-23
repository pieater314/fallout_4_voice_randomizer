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
from voice_randomizer.ba2 import VoiceArchive
from voice_randomizer.fuz import Fuz
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
        self.assertFalse((out / "mod").exists())
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


class ChaosModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / "game"
        self.data = self.game / "Data"
        self.data.mkdir(parents=True)
        (self.game / "Fallout4.exe").touch()
        self.work = self.root / "work"
        self.voices = {
            "sound/voice/fallout4.esm/one/a.fuz": Fuz(b"lip-a", audio("wav", b"A" * 20)),
            "sound/voice/fallout4.esm/two/b.fuz": Fuz(b"lip-b", audio("xwm", b"B" * 20)),
            "sound/voice/fallout4.esm/three/c.fuz": Fuz(b"lip-c", audio("wav", b"C" * 20)),
            "sound/voice/fallout4.esm/four/d.fuz": Fuz(b"lip-d", audio("wav", b"D" * 20)),
        }
        self.sounds = {
            "sound/fx/loop.wav": audio("wav", b"L" * 40, loop=(5, 14)),
            "sound/fx/b.xwm": audio("xwm", b"X" * 20),
            "sound/fx/c.wav": audio("wav", b"Y" * 20),
            "sound/fx/d.xwm": audio("xwm", b"Z" * 20),
        }
        self.write_voices()
        self.write_sounds()

    def write_voices(self, extra=()):
        entries = [(name.encode(), fuz.to_bytes(), False) for name, fuz in self.voices.items()]
        (self.data / "Fallout4 - Voices.ba2").write_bytes(archive_bytes(1, entries + list(extra)))

    def write_sounds(self, extra=()):
        entries = [(name.encode(), data, False) for name, data in self.sounds.items()]
        (self.data / "Fallout4 - Sounds.ba2").write_bytes(archive_bytes(1, entries + list(extra)))

    def run_build(self, work=None, **kwargs):
        return build(self.game, work or self.work, "loose", "seed", lambda *_: None,
                     lambda: False, mode="chaos", **kwargs)

    def test_mixed_mappings_preserve_destination_and_remove_source_loops(self):
        voices = list(self.voices)
        loop, sound_b, sound_c, sound_d = self.sounds
        mapping = {
            voices[0]: loop,
            loop: voices[1],
            voices[1]: voices[0],
            sound_b: sound_c,
            sound_c: sound_b,
            voices[2]: sound_d,
            sound_d: voices[3],
            voices[3]: voices[2],
        }
        conversions = []

        def fake_run(command, check_cancel):
            check_cancel()
            encoder = "--silent" in command
            source = Path(command[1] if encoder else command[command.index("-i") + 1])
            target = Path(command[2] if encoder else command[-1])
            content = source.read_bytes()
            data_pos = content.index(b"data")
            size = struct.unpack_from("<I", content, data_pos + 4)[0]
            payload = content[data_pos + 8:data_pos + 8 + size]
            target.write_bytes(audio("xwm" if encoder else "wav", payload))
            conversions.append("encode" if encoder else "decode")

        with patch("voice_randomizer.pipeline.shuffle_pool", return_value=mapping), \
             patch("voice_randomizer.sounds._run", side_effect=fake_run):
            out = self.run_build(create_zip=True)
        report = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(report["mode"], "chaos")
        self.assertEqual(
            {(item["source_type"], item["destination_type"]) for item in report["mapping"]},
            {("voice", "voice"), ("voice", "sound"), ("sound", "voice"), ("sound", "sound")},
        )
        self.assertIn("encode", conversions)
        self.assertIn("decode", conversions)
        with zipfile.ZipFile(out / "Fallout4VoiceRandomizer.zip") as archive:
            self.assertEqual(set(archive.namelist()), set(mapping))
            results = {name: archive.read(name) for name in archive.namelist()}
        self.assertFalse((out / "mod").exists())
        for destination, source in mapping.items():
            result = results[destination]
            destination_audio = self.voices[destination].audio if destination in self.voices else self.sounds[destination]
            source_audio = self.voices[source].audio if source in self.voices else self.sounds[source]
            if destination in self.voices:
                fuz = Fuz.parse(result)
                self.assertEqual(fuz.lip, self.voices[destination].lip)
                result = fuz.audio
            self.assertEqual(sound_format(result), sound_format(destination_audio))
            self.assertIn(source_audio[source_audio.index(b"data") + 8:source_audio.index(b"data") + 10], result)
            self.assertEqual(sound_format(result).loop is not None, destination == loop)
        scaled = sound_format(results[loop]).loop
        self.assertEqual(struct.unpack_from("<II", scaled.chunk, 44), (2, 7))
        log = (out / "randomization_log.txt").read_text(encoding="utf-8")
        self.assertIn("(sound wav -> voice wav)", log)

    def test_seed_exclusions_and_mapping(self):
        unsupported = Fuz(b"lip", audio("wav", cue=True)).to_bytes()
        self.write_voices([(b"sound/voice/fallout4.esm/npc/cue.fuz", unsupported, False)])
        self.write_sounds([(b"sound/fx/jingle.wav", audio("wav"), False)])
        with patch("voice_randomizer.pipeline.convert_sound",
                   side_effect=lambda data, source, destination, directory, check_cancel:
                   audio(destination.kind)):
            first = self.run_build()
            second = self.run_build(work=self.root / "other")
        reports = [json.loads((out / "manifest.json").read_text(encoding="utf-8"))
                   for out in (first, second)]
        self.assertEqual(reports[0]["mapping"], reports[1]["mapping"])
        mapping = reports[0]["mapping"]
        self.assertEqual({item["source"] for item in mapping},
                         {item["destination"] for item in mapping})
        self.assertTrue(all(item["source"] != item["destination"] for item in mapping))
        self.assertEqual({item["reason"] for item in reports[0]["exclusions"]},
                         {"Unsupported audio cue or loop metadata", "Music-related path"})
        self.assertIn("Excluded audio paths: 2",
                      (first / "randomization_log.txt").read_text(encoding="utf-8"))

    def test_invalid_missing_and_duplicate_inputs_do_not_publish(self):
        for case in ("missing_sounds", "no_voices", "no_sounds", "bad_voice", "duplicate", "ba2"):
            with self.subTest(case=case):
                self.write_voices()
                self.write_sounds()
                if case == "missing_sounds":
                    (self.data / "Fallout4 - Sounds.ba2").unlink()
                elif case == "no_voices":
                    self.voices, saved = {}, self.voices
                    self.write_voices([(b"other/file.txt", b"data", False)])
                    self.voices = saved
                elif case == "no_sounds":
                    self.sounds, saved = {}, self.sounds
                    self.write_sounds([(b"other/file.txt", b"data", False)])
                    self.sounds = saved
                elif case == "bad_voice":
                    self.write_voices([(b"sound/voice/fallout4.esm/npc/bad.fuz", b"bad", False)])
                elif case == "duplicate":
                    name, fuz = next(iter(self.voices.items()))
                    self.write_voices([(name.upper().encode(), fuz.to_bytes(), False)])
                message = {
                    "missing_sounds": "base game sound archive",
                    "no_voices": "No supported voice",
                    "no_sounds": "No supported sound",
                    "bad_voice": "Invalid voice",
                    "duplicate": "duplicate voice path",
                    "ba2": "loose files only",
                }[case]
                with self.assertRaisesRegex(ValueError, message):
                    build(self.game, self.work, "ba2" if case == "ba2" else "loose",
                          "seed", lambda *_: None, lambda: False, mode="chaos")
                self.assertFalse((self.work / "Fallout4VoiceRandomizer").exists())

    def test_conversion_failure_and_cancellation_do_not_publish(self):
        with patch("voice_randomizer.pipeline.convert_sound", side_effect=ValueError("conversion failed")):
            with self.assertRaisesRegex(ValueError, "Cannot replace .*conversion failed"):
                self.run_build()
        cancelled = threading.Event()

        def cancel_conversion(data, source, destination, directory, check_cancel):
            cancelled.set()
            check_cancel()

        with patch("voice_randomizer.pipeline.convert_sound", side_effect=cancel_conversion):
            with self.assertRaises(Cancelled):
                build(self.game, self.work, "loose", "seed", lambda *_: None,
                      cancelled.is_set, mode="chaos")
        self.assertFalse((self.work / "Fallout4VoiceRandomizer").exists())
        self.assertEqual(list(self.work.iterdir()), [])

    def test_changed_sound_archive_does_not_publish(self):
        source = self.data / "Fallout4 - Sounds.ba2"

        def progress(stage, current, total):
            if stage == "Processing sounds and voices" and current == total:
                with source.open("ab") as stream:
                    stream.write(b"changed")

        with patch("voice_randomizer.pipeline.convert_sound",
                   side_effect=lambda data, source, destination, directory, check_cancel:
                   audio(destination.kind)):
            with self.assertRaisesRegex(ValueError, "Input archive changed"):
                build(self.game, self.work, "loose", "seed", progress,
                      lambda: False, mode="chaos")
        self.assertFalse((self.work / "Fallout4VoiceRandomizer").exists())


class AustinModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / "game"
        self.data = self.game / "Data"
        self.data.mkdir(parents=True)
        (self.game / "Fallout4.exe").touch()
        self.austin = "Sound/Voice/Fallout4.esm/PlayerVoiceMale01/00190636_1.fuz"
        self.voices = {
            self.austin: Fuz(b"austin-lip", audio("wav", b"AA")),
        }
        for index in range(19):
            voice = "PlayerVoiceMale01" if index < 9 else "NPC"
            name = f"Sound/Voice/Fallout4.esm/{voice}/{index}.fuz"
            self.voices[name] = Fuz(f"lip-{index}".encode(), audio("wav", bytes([index, 0])))
        self.sounds = {
            f"sound/fx/{index}.wav": audio("wav", bytes([index, 1]))
            for index in range(20)
        }
        self.write_voices()
        (self.data / "Fallout4 - Sounds.ba2").write_bytes(archive_bytes(
            1, [(name.encode(), content, False) for name, content in self.sounds.items()]
        ))

    def write_voices(self):
        (self.data / "Fallout4 - Voices.ba2").write_bytes(archive_bytes(
            1, [(name.encode(), fuz.to_bytes(), False) for name, fuz in self.voices.items()]
        ))

    def test_selected_share_and_audio_for_each_mode_and_output(self):
        for mode, output_type in (
            ("player", "loose"), ("player", "ba2"),
            ("character", "loose"), ("character", "ba2"),
            ("sounds", "loose"), ("chaos", "loose"),
        ):
            with self.subTest(mode=mode, output=output_type):
                with patch("voice_randomizer.pipeline.secrets.token_hex", return_value="random-seed"):
                    out = build(self.game, self.root / f"{mode}-{output_type}", output_type,
                                "Austin.", lambda *_: None, lambda: False, mode=mode)
                report = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(report["seed"], "random-seed")
                self.assertTrue(report["austin_mode"])
                mapping = report["mapping"]
                expected_total = {"player": 10, "character": 20, "sounds": 20, "chaos": 40}[mode]
                self.assertEqual(len(mapping), expected_total)
                chosen = [item for item in mapping if item["source"] == self.austin]
                self.assertEqual(len(chosen), (expected_total * 35 + 50) // 100)
                if output_type == "ba2":
                    archive = VoiceArchive(out / "mod" / "VoiceRandomizer1 - Main.ba2")
                    results = {entry.path: archive.read(entry) for entry in archive.entries}
                else:
                    results = {item["destination"]: (out / "mod" / item["destination"]).read_bytes()
                               for item in mapping}
                for item in mapping:
                    destination = item["destination"]
                    result = results[destination]
                    if destination in self.voices:
                        fuz = Fuz.parse(result)
                        self.assertEqual(fuz.lip, self.voices[destination].lip)
                        result = fuz.audio
                    if item["source"] == self.austin:
                        self.assertEqual(result, self.voices[self.austin].audio)
                    else:
                        self.assertNotEqual(result, self.voices[self.austin].audio)

    def test_missing_source_fails_without_publishing(self):
        del self.voices[self.austin]
        self.write_voices()
        for mode in ("player", "character", "sounds", "chaos"):
            with self.subTest(mode=mode):
                work = self.root / mode
                with self.assertRaisesRegex(ValueError, "Required voice line not found"):
                    build(self.game, work, "loose", "Austin.", lambda *_: None,
                          lambda: False, mode=mode)
                self.assertFalse((work / "Fallout4VoiceRandomizer").exists())

    def test_xwm_source_is_converted_for_sound_and_voice_destinations(self):
        self.voices[self.austin] = Fuz(b"austin-lip", audio("xwm", b"AA"))
        self.write_voices()

        def fake_run(command, check_cancel):
            check_cancel()
            encoder = "--silent" in command
            source = Path(command[1] if encoder else command[command.index("-i") + 1])
            target = Path(command[2] if encoder else command[-1])
            content = source.read_bytes()
            data_pos = content.index(b"data")
            size = struct.unpack_from("<I", content, data_pos + 4)[0]
            payload = content[data_pos + 8:data_pos + 8 + size]
            target.write_bytes(audio("xwm" if encoder else "wav", payload))

        for mode in ("sounds", "chaos"):
            with self.subTest(mode=mode):
                with patch("voice_randomizer.pipeline.secrets.token_hex", return_value="random-seed"), \
                     patch("voice_randomizer.sounds._run", side_effect=fake_run):
                    out = build(self.game, self.root / mode, "loose", "Austin.",
                                lambda *_: None, lambda: False, mode=mode)
                report = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
                selected = [item for item in report["mapping"] if item["source"] == self.austin]
                self.assertTrue(selected)
                for item in selected:
                    destination = item["destination"]
                    result = (out / "mod" / destination).read_bytes()
                    if destination in self.voices:
                        result = Fuz.parse(result).audio
                    self.assertEqual(sound_format(result).kind, item["destination_format"])
                    self.assertIn(b"AA", result)

    def test_changed_source_archive_does_not_publish_in_sounds_mode(self):
        archive = self.data / "Fallout4 - Voices.ba2"

        def progress(stage, current, total):
            if stage == "Processing sounds" and current == total:
                with archive.open("ab") as stream:
                    stream.write(b"changed")

        work = self.root / "changed"
        with self.assertRaisesRegex(ValueError, "Input archive changed"):
            build(self.game, work, "loose", "Austin.", progress, lambda: False,
                  mode="sounds")
        self.assertFalse((work / "Fallout4VoiceRandomizer").exists())

    def test_trigger_is_case_sensitive(self):
        del self.voices[self.austin]
        self.write_voices()
        out = build(self.game, self.root / "normal", "loose", "austin.",
                    lambda *_: None, lambda: False, mode="player")
        report = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(report["seed"], "austin.")
        self.assertNotIn("austin_mode", report)


if __name__ == "__main__":
    unittest.main()
