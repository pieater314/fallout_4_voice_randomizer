"""Discover and convert sound effects without changing voice files."""

import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .audio import AudioConversionError, _converter_command, _run
from .ba2 import VoiceArchive
from .fuz import audio_format


class UnsupportedSound(ValueError):
    pass


@dataclass(frozen=True)
class SoundLoop:
    chunk: bytes
    frames: int


@dataclass(frozen=True)
class SoundFormat:
    kind: str
    channels: int
    rate: int
    bits: int
    # Loop metadata does not change the audio encoding.
    loop: SoundLoop | None = field(default=None, compare=False)


def sound_format(data: bytes) -> SoundFormat:
    kind = audio_format(data)
    position = 12
    fmt = None
    data_size = None
    sample = None
    while position < len(data):
        if position + 8 > len(data):
            raise ValueError("Incomplete RIFF chunk header.")
        name, size = struct.unpack_from("<4sI", data, position)
        position += 8
        end = position + size
        if end > len(data) or end + (size & 1) > len(data):
            raise ValueError("Incomplete RIFF chunk.")
        if name == b"fmt ":
            if fmt is not None or size < 16:
                raise ValueError("Invalid RIFF format chunk.")
            fmt = struct.unpack_from("<HHIIHH", data, position)
        elif name == b"data":
            if data_size is not None:
                raise ValueError("Multiple audio data chunks.")
            data_size = size
        elif name == b"smpl":
            if sample is not None or size < 36:
                raise ValueError("Incomplete sample metadata.")
            sample = data[position:end]
        elif name in (b"wsmp", b"cue "):
            raise UnsupportedSound("Unsupported audio cue or loop metadata")
        position = end + (size & 1)
    if fmt is None or data_size is None:
        raise ValueError("Audio format or data chunk is missing.")
    codec, channels, rate, _, block_align, bits = fmt
    if codec != (1 if kind == "wav" else 0x161):
        raise UnsupportedSound(f"Unsupported {kind.upper()} codec {codec}")
    if channels not in (1, 2) or rate not in (44100, 48000) or bits != 16:
        raise UnsupportedSound(
            f"Unsupported audio properties: {channels} channels, {rate} Hz, {bits} bits"
        )
    if kind == "xwm" and channels == 1 and rate == 48000:
        raise UnsupportedSound("XWM mono 48000 Hz encoding is not supported")
    loop = None
    if sample is not None:
        count, extra = struct.unpack_from("<II", sample, 28)
        if len(sample) < 36 + 24 * count + extra:
            raise ValueError("Incomplete sample loop metadata.")
        if count:
            if kind != "wav":
                raise UnsupportedSound("Embedded loops in XWM are not supported")
            if extra:
                raise UnsupportedSound("Unsupported sampler-specific loop metadata")
            if block_align != channels * (bits // 8) or data_size % block_align:
                raise ValueError("Invalid PCM audio data alignment.")
            frames = data_size // block_align
            for index in range(count):
                _, loop_type, start, end, fraction, _ = struct.unpack_from(
                    "<6I", sample, 36 + 24 * index
                )
                if loop_type != 0 or fraction:
                    raise UnsupportedSound("Unsupported sample loop type or fraction")
                if start > end or end >= frames:
                    raise ValueError("Sample loop points are outside the audio data.")
            loop = SoundLoop(sample, frames)
    return SoundFormat(kind, channels, rate, bits, loop)


def _replace_loop(data: bytes, destination: SoundFormat) -> bytes:
    output = bytearray(data[:12])
    position = 12
    data_size = None
    while position < len(data):
        name, size = struct.unpack_from("<4sI", data, position)
        end = position + 8 + size + (size & 1)
        if name == b"data":
            data_size = size
        if name != b"smpl":
            output.extend(data[position:end])
        position = end
    if destination.loop is not None:
        if data_size is None or destination.kind != "wav":
            raise AudioConversionError("Cannot add a loop to this audio format.")
        block_align = destination.channels * (destination.bits // 8)
        if data_size % block_align:
            raise AudioConversionError("Converted audio has invalid PCM alignment.")
        frames = data_size // block_align
        if not frames:
            raise AudioConversionError("Cannot add a loop to empty audio.")
        sample = bytearray(destination.loop.chunk)
        count = struct.unpack_from("<I", sample, 28)[0]
        for index in range(count):
            offset = 36 + 24 * index + 8
            start, end = struct.unpack_from("<II", sample, offset)
            new_start = start * frames // destination.loop.frames
            # The end frame is inclusive.
            new_end = (
                (end + 1) * frames + destination.loop.frames - 1
            ) // destination.loop.frames - 1
            struct.pack_into("<II", sample, offset, new_start, new_end)
        output.extend(b"smpl" + struct.pack("<I", len(sample)) + sample)
        if len(sample) & 1:
            output.append(0)
    struct.pack_into("<I", output, 4, len(output) - 8)
    return bytes(output)


def discover_sounds(game: Path) -> list[VoiceArchive]:
    data = game / "Data"
    if not (game / "Fallout4.exe").is_file() or not data.is_dir():
        raise ValueError("Select the Fallout 4 installation folder containing Fallout4.exe and Data.")
    files = {path.name.casefold(): path for path in data.iterdir() if path.is_file()}
    base = files.get("fallout4 - sounds.ba2")
    if base is None:
        raise ValueError("The base game sound archive was not found.")
    paths = [base]
    startup = files.get("fallout4 - startup.ba2")
    if startup is not None:
        paths.append(startup)
    for prefix in ("dlcrobot", "dlcworkshop01", "dlccoast", "dlcworkshop02",
                   "dlcworkshop03", "dlcnukaworld"):
        plugin = files.get(prefix + ".esm")
        archive = files.get(prefix + " - main.ba2")
        if bool(plugin) != bool(archive):
            raise ValueError(f"Incomplete DLC sound archive and plugin pair: {prefix}.")
        if archive is not None:
            paths.append(archive)
    return [VoiceArchive(path, sound_effects=True) for path in paths]


def convert_sound(data: bytes, source: SoundFormat, destination: SoundFormat,
                  directory: Path, check_cancel: Callable[[], None]) -> bytes:
    if source == destination:
        result = data
    else:
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except (ImportError, RuntimeError) as error:
            raise AudioConversionError(f"FFmpeg decoder is not available: {error}") from error
        with tempfile.TemporaryDirectory(prefix="sound-convert-", dir=directory) as temp:
            folder = Path(temp)
            input_path = folder / ("input.xwma" if source.kind == "xwm" else "input.wav")
            wav_path = folder / "output.wav"
            input_path.write_bytes(data)
            _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_path),
                  "-map", "0:a:0", "-ac", str(destination.channels), "-ar",
                  str(destination.rate), "-c:a", "pcm_s16le", str(wav_path)], check_cancel)
            result_path = wav_path
            if destination.kind == "xwm":
                result_path = folder / "output.xwma"
                _run([*_converter_command(None), str(wav_path), str(result_path), "--silent",
                      "--out-samplerate", str(destination.rate),
                      "--out-channels", str(destination.channels),
                      "--bitrate", "64" if destination.rate == 48000 else "48"], check_cancel)
            if not result_path.is_file():
                raise AudioConversionError("The converter did not create an output file.")
            result = result_path.read_bytes()
    try:
        actual = sound_format(result)
    except ValueError as error:
        raise AudioConversionError(f"Invalid converted audio: {error}") from error
    if actual != destination:
        raise AudioConversionError(f"Converted audio properties {actual} do not match {destination}.")
    if source.loop is not None or destination.loop is not None:
        result = _replace_loop(result, destination)
        try:
            actual = sound_format(result)
        except ValueError as error:
            raise AudioConversionError(f"Invalid converted audio: {error}") from error
        if (actual.loop is None) != (destination.loop is None):
            raise AudioConversionError("Converted audio has incorrect loop metadata.")
    return result
