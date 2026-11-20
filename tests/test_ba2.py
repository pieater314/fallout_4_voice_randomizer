import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from voice_randomizer.ba2 import VoiceArchive


def archive_bytes(version, entries):
    header = struct.Struct("<4sI4sIQ")
    record = struct.Struct("<I4sIIQIII")
    record_bytes = []
    payloads = []
    offset = header.size + record.size * len(entries)
    for name, data, compressed in entries:
        payload = zlib.compress(data) if compressed else data
        record_bytes.append(
            record.pack(0, b"fuz\0", 0, 0, offset, len(payload) if compressed else 0,
                        len(data), 0xBAADF00D)
        )
        payloads.append(payload)
        offset += len(payload)
    names = b"".join(struct.pack("<H", len(name)) + name for name, _, _ in entries)
    return header.pack(b"BTDX", version, b"GNRL", len(entries), offset) + b"".join(
        record_bytes + payloads
    ) + names


class VoiceArchiveTests(unittest.TestCase):
    def test_reads_version_7_and_compressed_player_entry(self):
        data = archive_bytes(7, [
            (b"other/\xff.bin", b"ignored", False),
            (b"sound\\voice\\game.esm\\playervoicemale01\\line.fuz", b"content", True),
        ])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "voice.ba2"
            path.write_bytes(data)
            archive = VoiceArchive(path)
            self.assertEqual(archive.version, 7)
            self.assertEqual(len(archive.entries), 1)
            self.assertEqual(archive.entries[0].voice, "playervoicemale01")
            self.assertEqual(archive.read(archive.entries[0]), b"content")

    def test_duplicate_paths_are_rejected(self):
        name = b"sound/voice/game.esm/playervoicefemale01/line.fuz"
        data = archive_bytes(1, [(name, b"1", False), (name.upper(), b"2", False)])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "voice.ba2"
            path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                VoiceArchive(path)

    def test_bad_record_and_unsupported_version_are_rejected(self):
        name = b"sound/voice/game.esm/playervoicefemale01/line.fuz"
        data = bytearray(archive_bytes(1, [(name, b"content", False)]))
        struct.pack_into("<I", data, 24 + 32, 0)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "voice.ba2"
            path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "invalid record"):
                VoiceArchive(path)
            data = archive_bytes(2, [(name, b"content", False)])
            path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "unsupported"):
                VoiceArchive(path)

    def test_truncated_compressed_entry_is_rejected(self):
        name = b"sound/voice/game.esm/playervoicefemale01/line.fuz"
        data = bytearray(archive_bytes(1, [(name, b"content", True)]))
        struct.pack_into("<I", data, 24 + 24, 1)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "voice.ba2"
            path.write_bytes(data)
            archive = VoiceArchive(path)
            with self.assertRaisesRegex(ValueError, "compressed"):
                archive.read(archive.entries[0])

    def test_unsafe_player_path_is_rejected(self):
        name = b"sound/voice/../playervoicefemale01/line.fuz"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "voice.ba2"
            path.write_bytes(archive_bytes(1, [(name, b"content", False)]))
            with self.assertRaisesRegex(ValueError, "unsafe"):
                VoiceArchive(path)


if __name__ == "__main__":
    unittest.main()
