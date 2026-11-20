import struct
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from voice_randomizer import audio
from voice_randomizer.fuz import Fuz, audio_format, replace_audio


def riff(kind: bytes, payload: bytes = b"") -> bytes:
    return b"RIFF" + struct.pack("<I", len(payload) + 4) + kind + payload


class FuzTests(unittest.TestCase):
    def test_converter_defaults_to_bundled_executable(self):
        bundled = Path(__file__).resolve().parents[1] / "vendor" / "WMAEncodeRS" / "WMAEncodeRS.exe"
        with patch.dict("os.environ", {}, clear=True), patch.object(audio.shutil, "which", return_value=None):
            self.assertEqual(audio._converter_command(None), [str(bundled)])

    def test_round_trip_and_replace_keeps_destination_lip(self):
        destination = Fuz(b"destination lip", riff(b"XWMA", b"old"))
        source = Fuz(b"source lip", riff(b"XWMA", b"new"))
        result = replace_audio(
            Fuz.parse(destination.to_bytes()), Fuz.parse(source.to_bytes())
        )
        self.assertEqual(Fuz.parse(result.to_bytes()), Fuz(destination.lip, source.audio))

    def test_recognizes_wav_and_xwm(self):
        self.assertEqual(audio_format(riff(b"WAVE")), "wav")
        self.assertEqual(audio_format(riff(b"XWMA")), "xwm")

    def test_converts_audio_format_mismatch(self):
        with TemporaryDirectory() as temp:
            converter = Path(temp) / "converter.py"
            converter.write_text(
                "import pathlib, sys\n"
                "source, target = map(pathlib.Path, sys.argv[1:])\n"
                "kind = b'WAVE' if target.suffix == '.wav' else b'XWMA'\n"
                "payload = source.read_bytes()[12:]\n"
                "target.write_bytes(b'RIFF' + (len(payload) + 4).to_bytes(4, 'little') + kind + payload)\n",
                encoding="utf-8",
            )
            cases = ((b"WAVE", b"XWMA", "wav"), (b"XWMA", b"WAVE", "xwm"))
            for destination_kind, source_kind, expected in cases:
                with self.subTest(destination=destination_kind, source=source_kind):
                    result = replace_audio(
                        Fuz(b"destination", riff(destination_kind, b"old")),
                        Fuz(b"source", riff(source_kind, b"new")),
                        [sys.executable, str(converter)],
                    )
                    self.assertEqual(audio_format(result.audio), expected)
                    self.assertEqual(result.lip, b"destination")
                    self.assertEqual(result.audio[12:], b"new")

    def test_rejects_invalid_fuz(self):
        invalid = [
            b"",
            struct.pack("<4sII", b"FUZE", 2, 0) + riff(b"XWMA"),
            struct.pack("<4sII", b"FUZE", 1, 100) + b"short",
            struct.pack("<4sII", b"FUZE", 1, 0) + b"not audio",
            struct.pack("<4sII", b"FUZE", 1, 0) + riff(b"XWMA")[:-1],
            struct.pack("<4sII", b"FUZE", 1, 0) + riff(b"OTHER"),
        ]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                Fuz.parse(data)


if __name__ == "__main__":
    unittest.main()
