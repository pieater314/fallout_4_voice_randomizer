"""Build a separate player voice mod from official Fallout 4 archives."""

import hashlib
import json
import re
import shutil
import struct
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .ba2 import VoiceArchive, VoiceEntry, _HEADER, _RECORD
from .fuz import Fuz, replace_audio
from .randomization import shuffle_pool
from . import __version__

_OFFICIAL = ("fallout4", "dlcrobot", "dlccoast", "dlcnukaworld", "dlcworkshop01",
             "dlcworkshop02", "dlcworkshop03")
Progress = Callable[[str, int, int], None]


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class Input:
    archive: VoiceArchive
    entry: VoiceEntry


def discover(game: Path, mode: str = "player") -> list[VoiceArchive]:
    data = game / "Data"
    if not (game / "Fallout4.exe").is_file() or not data.is_dir():
        raise ValueError("Select the Fallout 4 installation folder containing Fallout4.exe and Data.")
    
    if mode == "character":
        voice_filter = None
    else:
        from .ba2 import _VOICES
        voice_filter = _VOICES
    
    archives = []
    for prefix in _OFFICIAL:
        if prefix != "fallout4" and not any(
            p.name.casefold() == prefix + ".esm" for p in data.iterdir()
        ):
            continue
        candidates = sorted(
            (p for p in data.iterdir()
             if p.is_file() and p.suffix.casefold() == ".ba2"
             and re.fullmatch(re.escape(prefix) + r" - voices(?:_[a-z0-9-]+)?\.ba2",
                              p.name.casefold())),
            key=lambda p: p.name.casefold(),
        )
        if prefix == "fallout4" and not candidates:
            raise ValueError("The base game voice archive was not found.")
        if len(candidates) > 1:
            raise ValueError(f"Multiple voice archives found for {prefix}; select one language before running.")
        if candidates:
            archives.append(VoiceArchive(candidates[0], voice_filter=voice_filter))
    return archives


def _plugin() -> bytes:
    body = b"HEDR" + struct.pack("<HfII", 12, 1.0, 0, 0)
    body += b"CNAM" + struct.pack("<H", 17) + b"Voice Randomizer\0"
    body += b"INTV" + struct.pack("<HI", 4, 0)
    return struct.pack("<4sIIIII", b"TES4", len(body), 0x201, 0, 0, 0) + body


def _write_archive(path: Path, version: int, items: list[tuple[VoiceEntry, Path]],
                   cancelled: Callable[[], bool]) -> None:
    names = [entry.path.replace("/", "\\").encode("utf-8") for entry, _ in items]
    if any(len(name) > 65535 for name in names):
        raise ValueError("An archive path is too long.")
    with path.open("xb") as out:
        out.write(b"\0" * (_HEADER.size + len(items) * _RECORD.size))
        records = []
        for entry, source in items:
            if cancelled():
                raise Cancelled()
            offset = out.tell()
            size = source.stat().st_size
            if size > 0xFFFFFFFF:
                raise ValueError("A voice file is too large for BA2.")
            with source.open("rb") as stream:
                shutil.copyfileobj(stream, out)
            records.append(_RECORD.pack(entry.name_hash, entry.extension,
                                        entry.directory_hash, entry.flags, offset,
                                        0, size, 0xBAADF00D))
        names_offset = out.tell()
        for name in names:
            out.write(struct.pack("<H", len(name)))
            out.write(name)
        out.seek(0)
        out.write(_HEADER.pack(b"BTDX", version, b"GNRL", len(items), names_offset))
        for record in records:
            out.write(record)


def _publish(stage: Path, final: Path, progress: Progress,
             cancelled: Callable[[], bool]) -> None:
    files = [path for path in stage.rglob("*") if path.is_file()]
    total_bytes = sum(path.stat().st_size for path in files)
    progress_total = max(total_bytes, 1)
    copied_bytes = 0
    last_reported = 0
    progress("Publishing output", 0, progress_total)

    try:
        final.mkdir()
    except FileExistsError as error:
        raise FileExistsError(
            f"Output already exists: {final}. Move or rename it before running."
        ) from error

    def copy_file(source: str, destination: str) -> str:
        nonlocal copied_bytes, last_reported
        if cancelled():
            raise Cancelled()
        with open(source, "rb") as source_stream, open(destination, "wb") as destination_stream:
            while True:
                if cancelled():
                    raise Cancelled()
                chunk = source_stream.read(1024 * 1024)
                if not chunk:
                    break
                destination_stream.write(chunk)
                copied_bytes += len(chunk)
                if copied_bytes - last_reported >= 1024 * 1024:
                    progress("Publishing output", copied_bytes, progress_total)
                    last_reported = copied_bytes
        shutil.copystat(source, destination)
        return destination

    try:
        shutil.copytree(stage, final, dirs_exist_ok=True, copy_function=copy_file)
        if cancelled():
            raise Cancelled()
        progress("Publishing output", progress_total, progress_total)
    except Exception as error:
        try:
            shutil.rmtree(final)
        except OSError as cleanup_error:
            raise OSError(
                f"Could not publish output ({error}) and could not remove the "
                f"partial output folder ({cleanup_error}): {final}"
            ) from error
        raise


def _write_zip(mod: Path, target: Path, progress: Progress,
               cancelled: Callable[[], bool]) -> None:
    files = sorted(path for path in mod.rglob("*") if path.is_file())
    total = max(sum(path.stat().st_size for path in files), 1)
    copied = 0
    last_reported = 0
    hashes = {}
    progress("Creating ZIP", 0, total)
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_STORED) as archive:
        for path in files:
            if cancelled():
                raise Cancelled()
            name = path.relative_to(mod).as_posix()
            digest = hashlib.sha256()
            with path.open("rb") as source, archive.open(name, "w", force_zip64=True) as output:
                while True:
                    if cancelled():
                        raise Cancelled()
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    copied += len(chunk)
                    if copied - last_reported >= 1024 * 1024:
                        progress("Creating ZIP", copied, total)
                        last_reported = copied
            hashes[name] = digest.digest()
    progress("Creating ZIP", total, total)

    checked = 0
    last_reported = 0
    progress("Verifying ZIP", 0, total)
    with zipfile.ZipFile(target) as archive:
        if set(archive.namelist()) != set(hashes) or len(archive.namelist()) != len(hashes):
            raise ValueError("ZIP verification failed: unexpected archive entries.")
        for name, expected in hashes.items():
            if cancelled():
                raise Cancelled()
            digest = hashlib.sha256()
            with archive.open(name) as source:
                while True:
                    if cancelled():
                        raise Cancelled()
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    checked += len(chunk)
                    if checked - last_reported >= 1024 * 1024:
                        progress("Verifying ZIP", checked, total)
                        last_reported = checked
            if digest.digest() != expected:
                raise ValueError(f"ZIP verification failed: {name}")
    progress("Verifying ZIP", total, total)


def build(game: Path, work: Path, output_type: str, seed: str,
          progress: Progress, cancelled: Callable[[], bool],
          create_zip: bool = False, mode: str = "player") -> Path:
    game = game.resolve()
    work = work.resolve()
    if game == work or game in work.parents or work in game.parents:
        raise ValueError("Select a working folder outside the game installation.")
    if mode not in ("player", "character"):
        raise ValueError("Select 'player' or 'character' randomization mode.")
    if output_type not in ("loose", "ba2"):
        raise ValueError("Select loose files or BA2 output.")
    if not seed:
        raise ValueError("Enter a random seed.")
    work.mkdir(parents=True, exist_ok=True)
    final = work / "Fallout4VoiceRandomizer"
    if final.exists():
        raise FileExistsError(f"Output already exists: {final}. Move or rename it before running.")
    progress("Reading archives", 0, 1)
    archives = discover(game, mode=mode)
    identities = {}
    for archive in archives:
        stat = archive.path.stat()
        identities[archive.path] = (stat.st_size, stat.st_mtime_ns)
    by_path: dict[str, Input] = {}
    pools: dict[str, list[str]] = {}
    for archive in archives:
        for entry in archive.entries:
            key = entry.path.casefold()
            if key in by_path:
                raise ValueError(f"Duplicate voice path across archives: {entry.path}")
            by_path[key] = Input(archive, entry)
            if mode == "character":
                pool_key = entry.path.rsplit("/", 1)[0].casefold()
            else:
                pool_key = entry.voice
            pools.setdefault(pool_key, []).append(entry.path)
    if not by_path:
        if mode == "character":
            raise ValueError("No voice lines were found.")
        else:
            raise ValueError("No player voice lines were found.")
    mapping = {}
    for pool_key, paths in sorted(pools.items()):
        voice_seed = seed + ":" + pool_key
        mapping.update(shuffle_pool(paths, voice_seed))
    required = sum(item.entry.size for item in by_path.values()) * (2 if output_type == "ba2" else 1)
    if shutil.disk_usage(work).free < required:
        raise OSError("Not enough free space in the working folder.")
    if cancelled():
        raise Cancelled()

    with tempfile.TemporaryDirectory(prefix=".voice-randomizer-", dir=work) as temporary:
        stage = Path(temporary)
        mod = stage / "mod"
        mod.mkdir()
        files = stage / "files" if output_type == "ba2" else mod
        records: dict[Path, list[tuple[VoiceEntry, Path]]] = {}
        manifest = []
        total = len(mapping)
        for index, (destination, source) in enumerate(mapping.items(), 1):
            if cancelled():
                raise Cancelled()
            dst = by_path[destination.casefold()]
            src = by_path[source.casefold()]
            dst_fuz = Fuz.parse(dst.archive.read(dst.entry))
            src_fuz = Fuz.parse(src.archive.read(src.entry))
            result = replace_audio(dst_fuz, src_fuz).to_bytes()
            target = files.joinpath(*destination.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(result)
            records.setdefault(dst.archive.path, []).append((dst.entry, target))
            manifest.append({"destination": destination, "source": source,
                             "destination_archive": dst.archive.path.name,
                             "source_archive": src.archive.path.name})
            if index % 100 == 0 or index == total:
                progress("Processing voices", index, total)
        if output_type == "ba2":
            for index, archive in enumerate(archives, 1):
                if cancelled():
                    raise Cancelled()
                items = records.get(archive.path, [])
                if not items:
                    continue
                name = f"VoiceRandomizer{index}"
                target = mod / f"{name} - Main.ba2"
                progress("Writing BA2 archives", index, len(archives))
                _write_archive(target, archive.version, items, cancelled)
                mod.joinpath(name + ".esl").write_bytes(_plugin())
                check = VoiceArchive(target)
                if check.version != archive.version or len(check.entries) != len(items):
                    raise ValueError(f"Archive verification failed: {target.name}")
                for entry in check.entries:
                    if cancelled():
                        raise Cancelled()
                    original = files.joinpath(*entry.path.split("/"))
                    if hashlib.sha256(check.read(entry)).digest() != hashlib.sha256(original.read_bytes()).digest():
                        raise ValueError(f"Archive verification failed: {entry.path}")
            shutil.rmtree(files)
        for archive in archives:
            stat = archive.path.stat()
            if (stat.st_size, stat.st_mtime_ns) != identities[archive.path]:
                raise ValueError(f"Input archive changed during the run: {archive.path.name}")
        report = {"application_version": __version__,
                  "seed": seed, "output": output_type,
                  "archives": [{"name": a.path.name, "version": a.version,
                                "size": identities[a.path][0],
                                "mtime_ns": identities[a.path][1]} for a in archives],
                  "mapping": manifest}
        (stage / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        log_lines = [
            f"Fallout 4 Voice Randomizer {__version__} mapping",
            f"Seed: {seed}",
            "",
        ]
        log_lines.extend(
            f"{item['source_archive']}!{item['source']} -> "
            f"{item['destination_archive']}!{item['destination']}"
            for item in manifest
        )
        (stage / "randomization_log.txt").write_text(
            "\n".join(log_lines) + "\n", encoding="utf-8"
        )
        if create_zip:
            mod_files = [path for path in mod.rglob("*") if path.is_file()]
            zip_estimate = sum(
                path.stat().st_size + 2 * len(path.relative_to(mod).as_posix().encode("utf-8")) + 256
                for path in mod_files
            ) + 1024
            stage_size = sum(path.stat().st_size for path in stage.rglob("*") if path.is_file())
            if shutil.disk_usage(work).free < stage_size + 2 * zip_estimate:
                raise OSError("Not enough free space to create and publish the ZIP and mod files.")
            _write_zip(mod, stage / "Fallout4VoiceRandomizer.zip", progress, cancelled)
        # Publish only after validation; remove partial output if copying fails.
        if cancelled():
            raise Cancelled()
        _publish(stage, final, progress, cancelled)
    return final
