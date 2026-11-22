"""Read player voice entries from Fallout 4 GNRL BA2 archives."""

import os
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path


_HEADER = struct.Struct("<4sI4sIQ")
_RECORD = struct.Struct("<I4sIIQIII")
_VOICES = {b"playervoicemale01", b"playervoicefemale01"}
_MAX_VOICE_SIZE = 256 * 1024 * 1024


@dataclass(frozen=True)
class VoiceEntry:
    path: str
    voice: str
    offset: int
    packed_size: int
    size: int
    name_hash: int = 0
    extension: bytes = b"fuz\0"
    directory_hash: int = 0
    flags: int = 0


class VoiceArchive:
    def __init__(self, path: Path, voice_filter: set[bytes] | None = None,
                 *, sound_effects: bool = False):
        """Read voice entries from a BA2 archive.
        
        Args:
            path: Path to the BA2 archive file
            voice_filter: Set of voice types to include (e.g. {b"playervoicemale01", b"playervoicefemale01"})
                         If None, includes all voice types
        """
        self.path = Path(path)
        self.version = 0
        self.entries: list[VoiceEntry] = []
        if voice_filter is None:
            voice_filter = set()
        with self.path.open("rb") as stream:
            file_size = os.fstat(stream.fileno()).st_size
            header = stream.read(_HEADER.size)
            if len(header) != _HEADER.size:
                raise ValueError(f"{path}: incomplete BA2 header")
            magic, version, kind, count, names_offset = _HEADER.unpack(header)
            if magic != b"BTDX" or kind != b"GNRL" or version not in (1, 7, 8):
                raise ValueError(f"{path}: unsupported BA2 format or version")
            record_end = _HEADER.size + count * _RECORD.size
            if record_end > file_size or not record_end <= names_offset < file_size:
                raise ValueError(f"{path}: invalid BA2 table offsets")
            self.version = version
            stream.seek(names_offset)
            selected = []
            seen = set()
            for index in range(count):
                raw_length = stream.read(2)
                if len(raw_length) != 2:
                    raise ValueError(f"{path}: incomplete BA2 name table")
                length = struct.unpack("<H", raw_length)[0]
                name = stream.read(length)
                if len(name) != length:
                    raise ValueError(f"{path}: incomplete BA2 name")
                parts = name.lower().replace(b"\\", b"/").split(b"/")
                if sound_effects:
                    if len(parts) < 3 or parts[:2] != [b"sound", b"fx"]:
                        continue
                    voice_type = b""
                else:
                    if (
                        len(parts) < 5
                        or parts[:2] != [b"sound", b"voice"]
                        or not parts[-1].endswith(b".fuz")
                    ):
                        continue
                    voice_type = parts[3] if len(parts) == 5 else parts[-2]
                    if voice_filter and voice_type not in voice_filter:
                        continue
                if any(
                    part in (b"", b".", b"..")
                    or part.endswith((b".", b" "))
                    or any(char < 32 or char in b'<>:"|?*' for char in part)
                    or part.split(b".", 1)[0].upper() in (
                        b"CON", b"PRN", b"AUX", b"NUL",
                        *(f"COM{i}".encode() for i in range(1, 10)),
                        *(f"LPT{i}".encode() for i in range(1, 10)),
                    )
                    for part in parts
                ):
                    raise ValueError(f"{path}: unsafe {'sound' if sound_effects else 'voice'} path")
                try:
                    decoded = name.decode("utf-8").replace("\\", "/")
                except UnicodeDecodeError:
                    if sound_effects:
                        raise ValueError(f"{path}: invalid sound path encoding")
                    continue
                key = decoded.casefold()
                if key in seen and not sound_effects:
                    raise ValueError(f"{path}: duplicate voice path: {decoded}")
                seen.add(key)
                selected.append((index, decoded, voice_type.decode("ascii")))

            for index, name, voice in selected:
                stream.seek(_HEADER.size + index * _RECORD.size)
                raw = stream.read(_RECORD.size)
                name_hash, extension, directory_hash, flags, offset, packed, size, tail = _RECORD.unpack(raw)
                stored = packed or size
                if (
                    tail != 0xBAADF00D
                    or size > _MAX_VOICE_SIZE
                    or packed > _MAX_VOICE_SIZE
                    or offset < record_end
                    or offset + stored > file_size
                ):
                    raise ValueError(f"{path}: invalid record for {name}")
                self.entries.append(VoiceEntry(name, voice, offset, packed, size,
                                               name_hash, extension, directory_hash, flags))
        self._entry_set = frozenset(self.entries)

    def read(self, entry: VoiceEntry) -> bytes:
        if entry not in self._entry_set:
            raise ValueError("The voice entry does not belong to this archive.")
        with self.path.open("rb") as stream:
            stream.seek(entry.offset)
            data = stream.read(entry.packed_size or entry.size)
        if len(data) != (entry.packed_size or entry.size):
            raise ValueError(f"{self.path}: truncated voice entry: {entry.path}")
        if entry.packed_size:
            decoder = zlib.decompressobj()
            data = decoder.decompress(data, entry.size + 1)
            if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise ValueError(f"{self.path}: invalid compressed voice entry: {entry.path}")
        if len(data) != entry.size:
            raise ValueError(f"{self.path}: incorrect voice entry size: {entry.path}")
        return data
