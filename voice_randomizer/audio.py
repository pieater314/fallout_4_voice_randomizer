"""Convert Fallout audio with the WMAEncodeRS command-line tool."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from .fuz import AudioFormat, audio_format


class AudioConversionError(ValueError):
    """Raised when the required audio converter is unavailable or fails."""


ConverterCommand = str | os.PathLike[str] | Sequence[str]


def _converter_command(converter: ConverterCommand | None) -> list[str]:
    if converter is not None:
        if isinstance(converter, (str, os.PathLike)):
            return [os.fspath(converter)]
        return list(converter)
    configured = os.environ.get("WMAENCODERS_PATH")
    if configured:
        return [configured]
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    bundled = bundle_root / "vendor" / "WMAEncodeRS" / "WMAEncodeRS.exe"
    if bundled.is_file():
        return [str(bundled)]
    for name in ("WMAEncodeRS.exe", "WMAEncodeRS"):
        path = shutil.which(name)
        if path:
            return [path]
    raise AudioConversionError(
        "Audio conversion requires WMAEncodeRS. Put WMAEncodeRS.exe on PATH "
        "or set WMAENCODERS_PATH to its executable."
    )


def convert_audio(
    audio: bytes,
    source_format: AudioFormat,
    target_format: AudioFormat,
    converter: ConverterCommand | None = None,
) -> bytes:
    """Convert a validated XWMA or WAV payload to the target format."""
    if source_format == target_format:
        return audio
    command = _converter_command(converter)
    source_suffix = ".xwma" if source_format == "xwm" else ".wav"
    target_suffix = ".xwma" if target_format == "xwm" else ".wav"
    with tempfile.TemporaryDirectory(prefix="voice-randomizer-audio-") as directory:
        folder = Path(directory)
        source_path = folder / f"source{source_suffix}"
        target_path = folder / f"converted{target_suffix}"
        source_path.write_bytes(audio)
        try:
            result = subprocess.run(
                [*command, str(source_path), str(target_path)],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            raise AudioConversionError(
                f"Could not run WMAEncodeRS: {error}"
            ) from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            message = f"Audio conversion failed with exit code {result.returncode}."
            if detail:
                message += f" {detail}"
            raise AudioConversionError(message)
        if not target_path.is_file():
            raise AudioConversionError(
                "WMAEncodeRS completed without creating the converted audio file."
            )
        converted = target_path.read_bytes()
    try:
        actual_format = audio_format(converted)
    except ValueError as error:
        raise AudioConversionError(
            "WMAEncodeRS produced an invalid XWMA or WAV payload."
        ) from error
    if actual_format != target_format:
        raise AudioConversionError(
            f"WMAEncodeRS produced {actual_format}, expected {target_format}."
        )
    return converted
