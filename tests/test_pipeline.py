import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from test_ba2 import archive_bytes
from test_fuz import riff
from voice_randomizer import __version__
from voice_randomizer.audio import AudioConversionError
from voice_randomizer.ba2 import VoiceArchive
from voice_randomizer.fuz import Fuz
from voice_randomizer.pipeline import Cancelled, build


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / "game"
        data = self.game / "Data"
        data.mkdir(parents=True)
        (self.game / "Fallout4.exe").touch()
        self.work = self.root / "work"
        self.entries = []
        for voice in ("playervoicemale01", "playervoicefemale01"):
            for number in range(2):
                name = f"sound/voice/fallout4.esm/{voice}/{number}.fuz"
                fuz = Fuz(f"{voice}-{number}".encode(), riff(b"XWMA", bytes([number]))).to_bytes()
                self.entries.append((name.encode(), fuz, False))
        (data / "Fallout4 - Voices.ba2").write_bytes(archive_bytes(1, self.entries))

    def test_loose_output_keeps_lips_and_shuffles_each_pool(self):
        out = build(self.game, self.work, "loose", "seed", lambda *_: None, lambda: False)
        self.assertFalse((out / "Fallout4VoiceRandomizer.zip").exists())
        report = json.loads((out / "manifest.json").read_text())
        self.assertEqual(report["application_version"], __version__)
        self.assertEqual(len(report["mapping"]), 4)
        log = (out / "randomization_log.txt").read_text(encoding="utf-8")
        self.assertIn(f"Fallout 4 Voice Randomizer {__version__} mapping", log)
        self.assertIn("Seed: seed", log)
        for item in report["mapping"]:
            self.assertIn(
                f"{item['source_archive']}!{item['source']} -> "
                f"{item['destination_archive']}!{item['destination']}",
                log,
            )
        originals = {name.decode(): Fuz.parse(data) for name, data, _ in self.entries}
        for item in report["mapping"]:
            destination, source = item["destination"], item["source"]
            result = Fuz.parse((out / "mod" / destination).read_bytes())
            self.assertNotEqual(destination, source)
            self.assertEqual(destination.split("/")[3], source.split("/")[3])
            self.assertEqual(result.lip, originals[destination].lip)
            self.assertEqual(result.audio, originals[source].audio)

    def test_ba2_output_preserves_version_and_entries(self):
        source = self.game / "Data" / "Fallout4 - Voices.ba2"
        source.write_bytes(archive_bytes(7, self.entries))
        out = build(self.game, self.work, "ba2", "seed", lambda *_: None, lambda: False)
        mod = out / "mod"
        archive = VoiceArchive(mod / "VoiceRandomizer1 - Main.ba2")
        self.assertEqual(archive.version, 7)
        self.assertEqual(len(archive.entries), 4)
        self.assertEqual((mod / "VoiceRandomizer1.esl").read_bytes()[:4], b"TES4")
        self.assertFalse((out / "files").exists())

    def test_failure_and_cancellation_do_not_publish(self):
        with self.assertRaises(Cancelled):
            build(self.game, self.work, "loose", "seed", lambda *_: None, lambda: True)
        self.assertFalse((self.work / "Fallout4VoiceRandomizer").exists())
        name, _, _ = self.entries[0]
        self.entries[0] = (name, Fuz(b"", riff(b"WAVE")).to_bytes(), False)
        (self.game / "Data" / "Fallout4 - Voices.ba2").write_bytes(archive_bytes(1, self.entries))
        with patch(
            "voice_randomizer.audio.convert_audio",
            side_effect=AudioConversionError("Audio conversion unavailable."),
        ):
            with self.assertRaisesRegex(ValueError, "conversion"):
                build(self.game, self.work, "loose", "seed", lambda *_: None, lambda: False)
        self.assertFalse((self.work / "Fallout4VoiceRandomizer").exists())

    def test_never_writes_inside_game_or_over_existing_output(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            build(self.game, self.game / "work", "loose", "seed", lambda *_: None, lambda: False)
        build(self.game, self.work, "loose", "seed", lambda *_: None, lambda: False)
        with self.assertRaises(FileExistsError):
            build(self.game, self.work, "loose", "seed", lambda *_: None, lambda: False)

    def test_publishes_without_renaming_staging_directory(self):
        with patch(
            "os.rename",
            side_effect=PermissionError("Directory rename denied."),
        ):
            out = build(
                self.game, self.work, "loose", "seed", lambda *_: None, lambda: False
            )
        self.assertTrue((out / "mod").is_dir())
        self.assertTrue((out / "manifest.json").is_file())
        self.assertTrue((out / "randomization_log.txt").is_file())

    def test_reports_progress_while_publishing(self):
        events = []
        build(
            self.game,
            self.work,
            "loose",
            "seed",
            lambda *event: events.append(event),
            lambda: False,
        )
        publishing = [event for event in events if event[0] == "Publishing output"]
        self.assertTrue(publishing)
        self.assertEqual(publishing[0][1], 0)
        self.assertEqual(publishing[-1][1], publishing[-1][2])

    def test_optional_zip_contains_only_mod_files_for_both_output_types(self):
        for output_type in ("loose", "ba2"):
            with self.subTest(output_type=output_type):
                events = []
                out = build(
                    self.game, self.work / output_type, output_type, "seed",
                    lambda *event: events.append(event), lambda: False, create_zip=True,
                )
                mod = out / "mod"
                files = {
                    path.relative_to(mod).as_posix(): path.read_bytes()
                    for path in mod.rglob("*") if path.is_file()
                }
                with zipfile.ZipFile(out / "Fallout4VoiceRandomizer.zip") as archive:
                    self.assertEqual(set(archive.namelist()), set(files))
                    for name, content in files.items():
                        self.assertEqual(archive.read(name), content)
                self.assertIn("Creating ZIP", [event[0] for event in events])
                self.assertIn("Verifying ZIP", [event[0] for event in events])

    def test_cancel_during_zip_does_not_publish(self):
        cancelled = False

        def progress(stage, current, total):
            nonlocal cancelled
            if stage == "Verifying ZIP":
                cancelled = True

        with self.assertRaises(Cancelled):
            build(
                self.game, self.work, "loose", "seed",
                progress, lambda: cancelled, create_zip=True,
            )
        self.assertFalse((self.work / "Fallout4VoiceRandomizer").exists())

    def test_zip_failure_does_not_publish(self):
        with patch("voice_randomizer.pipeline._write_zip", side_effect=OSError("ZIP failed")):
            with self.assertRaisesRegex(OSError, "ZIP failed"):
                build(
                    self.game, self.work, "ba2", "seed",
                    lambda *_: None, lambda: False, create_zip=True,
                )
        self.assertFalse((self.work / "Fallout4VoiceRandomizer").exists())

    def test_changed_input_does_not_publish(self):
        source = self.game / "Data" / "Fallout4 - Voices.ba2"

        def progress(stage, current, total):
            if stage == "Processing voices" and current == total:
                with source.open("ab") as stream:
                    stream.write(b"changed")

        with self.assertRaisesRegex(ValueError, "changed during the run"):
            build(self.game, self.work, "loose", "seed", progress, lambda: False)
        self.assertFalse((self.work / "Fallout4VoiceRandomizer").exists())

    def test_character_mode_keeps_voices_within_each_character(self):
        entries = []
        for character in ("companionhooper", "npcmpaladindanse"):
            for voice in ("playervoicemale01", "playervoicefemale01", "othervoicetype"):
                for number in range(2):
                    name = f"sound/voice/fallout4.esm/{character}/{voice}{number}.fuz"
                    fuz = Fuz(f"{voice}-{number}".encode(), riff(b"XWMA", bytes([number]))).to_bytes()
                    entries.append((name.encode(), fuz, False))
        (self.game / "Data" / "Fallout4 - Voices.ba2").write_bytes(archive_bytes(1, entries))
        
        out = build(self.game, self.work, "loose", "seed", lambda *_: None, lambda: False, mode="character")
        report = json.loads((out / "manifest.json").read_text())
        self.assertEqual(len(report["mapping"]), 12)
        
        for item in report["mapping"]:
            destination = item["destination"]
            source = item["source"]
            dest_parts = destination.split("/")
            src_parts = source.split("/")
            
            self.assertNotEqual(destination, source)
            self.assertEqual(dest_parts[0], src_parts[0])
            self.assertEqual(dest_parts[1], src_parts[1])
            self.assertEqual(dest_parts[2], src_parts[2])
            self.assertEqual(dest_parts[3], src_parts[3])
            
            self.assertIn(destination, [e[0].decode() for e in entries])
            self.assertIn(source, [e[0].decode() for e in entries])

    def test_character_mode_realistic_paths_stay_in_their_folder(self):
        names = [
            "Sound/Voice/Fallout4.esm/PlayerVoiceFemale01/000B8526_1.fuz",
            "sound/voice/fallout4.esm/playervoicefemale01/000B8547_1.fuz",
            "Sound/Voice/Fallout4.esm/PlayerVoiceFemale01/000B854B_1.fuz",
            "Sound/Voice/Fallout4.esm/PlayerVoiceMale01/0005D7A7_1.fuz",
            "Sound/Voice/Fallout4.esm/PlayerVoiceMale01/0005D7A8_1.fuz",
            "Sound/Voice/Fallout4.esm/NPCFCricket/0012FCBE_1.fuz",
            "Sound/Voice/Fallout4.esm/NPCFCricket/0012FCBF_1.fuz",
            "Sound/Voice/Fallout4.esm/MaleOld/001357E4_1.fuz",
            "Sound/Voice/DLCCoast.esm/PlayerVoiceFemale01/00048029_1.fuz",
            "Sound/Voice/DLCCoast.esm/PlayerVoiceFemale01/00048030_1.fuz",
            "Sound/Voice/DLCCoast.esm/DLC03FemaleMariner/00048031_1.fuz",
            "Sound/Voice/DLCCoast.esm/DLC03FemaleMariner/00048032_1.fuz",
        ]
        entries = [
            (name.encode(), Fuz(f"lip-{index}".encode(),
                                riff(b"XWMA", bytes([index]))).to_bytes(), False)
            for index, name in enumerate(names)
        ]
        data = self.game / "Data"
        (data / "Fallout4 - Voices.ba2").write_bytes(archive_bytes(1, entries[:8]))
        (data / "DLCCoast.esm").touch()
        (data / "DLCCoast - Voices_en.ba2").write_bytes(archive_bytes(1, entries[8:]))

        out = build(self.game, self.work, "loose", "seed", lambda *_: None,
                    lambda: False, mode="character")
        mapping = json.loads((out / "manifest.json").read_text())["mapping"]
        originals = {name.decode(): Fuz.parse(content) for name, content, _ in entries}
        self.assertEqual(len(mapping), len(names))
        self.assertEqual({item["destination"] for item in mapping}, set(names))
        self.assertEqual({item["source"] for item in mapping}, set(names))
        for item in mapping:
            destination, source = item["destination"], item["source"]
            folder = destination.rsplit("/", 1)[0].casefold()
            self.assertEqual(folder, source.rsplit("/", 1)[0].casefold())
            pool_size = sum(name.rsplit("/", 1)[0].casefold() == folder for name in names)
            if pool_size == 1:
                self.assertEqual(destination, source)
            else:
                self.assertNotEqual(destination, source)
            result = Fuz.parse((out / "mod" / destination).read_bytes())
            self.assertEqual(result.lip, originals[destination].lip)
            self.assertEqual(result.audio, originals[source].audio)

