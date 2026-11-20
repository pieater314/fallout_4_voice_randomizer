"""Read version 1 FUZ containers and replace audio."""

import struct
from dataclasses import dataclass
from typing import Literal


AudioFormat = Literal["xwm", "wav"]
_HEADER = struct.Struct("<4sII")


def audio_format(audio: bytes) -> AudioFormat:
    if len(audio) < 12 or audio[:4] != b"RIFF":
        raise ValueError("The audio payload is not a RIFF file.")
    if struct.unpack_from("<I", audio, 4)[0] + 8 != len(audio):
        raise ValueError("The RIFF length does not match the audio payload.")
    if audio[8:12] == b"XWMA":
        return "xwm"
    if audio[8:12] == b"WAVE":
        return "wav"
    raise ValueError("The audio payload is not XWM or WAV.")


@dataclass(frozen=True)
class Fuz:
    lip: bytes
    audio: bytes

    @classmethod
    def parse(cls, data: bytes) -> "Fuz":
        if len(data) < _HEADER.size:
            raise ValueError("The FUZ header is incomplete.")
        magic, version, lip_size = _HEADER.unpack_from(data)
        if magic != b"FUZE" or version != 1:
            raise ValueError("The FUZ signature or version is not supported.")
        audio_offset = _HEADER.size + lip_size
        if audio_offset > len(data):
            raise ValueError("The FUZ lip data is incomplete.")
        audio = data[audio_offset:]
        audio_format(audio)
        return cls(data[_HEADER.size:audio_offset], audio)

    def to_bytes(self) -> bytes:
        audio_format(self.audio)
        return _HEADER.pack(b"FUZE", 1, len(self.lip)) + self.lip + self.audio


def replace_audio(destination: Fuz, source: Fuz, converter=None) -> Fuz:
    destination_format = audio_format(destination.audio)
    source_format = audio_format(source.audio)
    if destination_format == source_format:
        return Fuz(destination.lip, source.audio)
    from .audio import convert_audio

    converted = convert_audio(
        source.audio, source_format, destination_format, converter
    )
    return Fuz(destination.lip, converted)
