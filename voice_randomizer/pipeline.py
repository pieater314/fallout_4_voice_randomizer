"""Build a separate player voice mod from official Fallout 4 archives."""

import hashlib
import json
import re
import shutil
import struct
import tempfile
import zipfile
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event
from typing import Callable

from .ba2 import VoiceArchive, VoiceEntry, _HEADER, _RECORD
from .fuz import Fuz, replace_audio
from .randomization import shuffle_pool
from .sounds import SoundFormat, UnsupportedSound, convert_sound, discover_sounds, sound_format
from . import __version__

_OFFICIAL = ("fallout4", "dlcrobot", "dlccoast", "dlcnukaworld", "dlcworkshop01",
             "dlcworkshop02", "dlcworkshop03")
_SOUND_WORKERS = 4
_VOICE_WORKERS = 4
Progress = Callable[[str, int, int], None]


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class Input:
    archive: VoiceArchive
    entry: VoiceEntry
    sound: bool = False


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


def _write_entries(mapping: dict[str, str], by_path: dict[str, Input],
                   formats: dict[str, SoundFormat], stage: Path, files: Path,
                   work: Path, progress: Progress, cancelled: Callable[[], bool],
                   *, mode: str) -> tuple[list[dict[str, str]],
                                          dict[Path, list[tuple[VoiceEntry, Path]]]]:
    stop = Event()
    tasks = iter(enumerate(mapping.items()))
    pending: dict[Future[bytes], tuple[int, str, str, Input, Input]] = {}
    manifest: dict[int, dict[str, str]] = {}
    written: dict[int, tuple[Path, VoiceEntry, Path]] = {}
    total = len(mapping)
    completed = 0
    audio_mode = mode in ("sounds", "chaos")
    workers = _SOUND_WORKERS if audio_mode else _VOICE_WORKERS

    def check_cancel() -> None:
        if stop.is_set() or cancelled():
            raise Cancelled()

    def convert(destination: str, source: str, dst: Input, src: Input) -> bytes:
        check_cancel()
        try:
            if mode == "chaos":
                source_data = src.archive.read(src.entry)
                check_cancel()
                source_audio = source_data if src.sound else Fuz.parse(source_data).audio
                destination_fuz = None if dst.sound else Fuz.parse(dst.archive.read(dst.entry))
                destination_format = formats[destination.casefold()]
                if destination_fuz is not None:
                    destination_format = replace(destination_format, loop=None)
                result = convert_sound(
                    source_audio, formats[source.casefold()], destination_format,
                    stage, check_cancel,
                )
                if destination_fuz is not None:
                    result = Fuz(destination_fuz.lip, result).to_bytes()
            elif mode == "sounds":
                data = src.archive.read(src.entry)
                check_cancel()
                result = convert_sound(
                    data, formats[source.casefold()], formats[destination.casefold()],
                    stage, check_cancel,
                )
            else:
                dst_fuz = Fuz.parse(dst.archive.read(dst.entry))
                check_cancel()
                src_fuz = Fuz.parse(src.archive.read(src.entry))
                check_cancel()
                result = replace_audio(dst_fuz, src_fuz, check_cancel=check_cancel).to_bytes()
            check_cancel()
            return result
        except ValueError as error:
            raise ValueError(
                f"Cannot replace {dst.archive.path.name}!{destination} with "
                f"{src.archive.path.name}!{source}: {error}"
            ) from error

    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        def submit_tasks() -> None:
            while len(pending) < workers:
                check_cancel()
                try:
                    index, (destination, source) = next(tasks)
                except StopIteration:
                    break
                dst = by_path[destination.casefold()]
                src = by_path[source.casefold()]
                future = executor.submit(convert, destination, source, dst, src)
                pending[future] = (index, destination, source, dst, src)

        submit_tasks()
        while pending:
            check_cancel()
            done, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            for future in done:
                index, destination, source, dst, src = pending.pop(future)
                result = future.result()
                check_cancel()
                if audio_mode and shutil.disk_usage(work).free < 2 * len(result):
                    kind = "sound" if mode == "sounds" else "audio"
                    raise OSError(f"Not enough free space to stage and publish the {kind} output.")
                target = files.joinpath(*destination.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(result)
                if audio_mode and hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(result).digest():
                    kind = "Sound" if mode == "sounds" else "Audio"
                    raise ValueError(f"{kind} output verification failed: {destination}")
                manifest[index] = {
                    "destination": destination, "source": source,
                    "destination_archive": dst.archive.path.name,
                    "source_archive": src.archive.path.name,
                }
                if audio_mode:
                    manifest[index]["source_format"] = formats[source.casefold()].kind
                    manifest[index]["destination_format"] = formats[destination.casefold()].kind
                    if mode == "chaos":
                        manifest[index]["source_type"] = "sound" if src.sound else "voice"
                        manifest[index]["destination_type"] = "sound" if dst.sound else "voice"
                else:
                    written[index] = (dst.archive.path, dst.entry, target)
                completed += 1
                if completed % 100 == 0 or completed == total:
                    stage_name = ("Processing sounds and voices" if mode == "chaos"
                                  else "Processing sounds" if mode == "sounds"
                                  else "Processing voices")
                    progress(stage_name, completed, total)
            submit_tasks()
    finally:
        stop.set()
        for future in pending:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
    records: dict[Path, list[tuple[VoiceEntry, Path]]] = {}
    for index in sorted(written):
        archive, entry, target = written[index]
        records.setdefault(archive, []).append((entry, target))
    return [manifest[index] for index in sorted(manifest)], records


def build(game: Path, work: Path, output_type: str, seed: str,
          progress: Progress, cancelled: Callable[[], bool],
          create_zip: bool = False, mode: str = "player") -> Path:
    game = game.resolve()
    work = work.resolve()
    if game == work or game in work.parents or work in game.parents:
        raise ValueError("Select a working folder outside the game installation.")
    if mode not in ("player", "character", "sounds", "chaos"):
        raise ValueError("Select 'player', 'character', 'sounds', or 'chaos' randomization mode.")
    if output_type not in ("loose", "ba2"):
        raise ValueError("Select loose files or BA2 output.")
    if mode in ("sounds", "chaos") and output_type != "loose":
        raise ValueError(f"{'Sounds-only' if mode == 'sounds' else 'Chaos'} mode supports loose files only.")
    if not seed:
        raise ValueError("Enter a random seed.")
    work.mkdir(parents=True, exist_ok=True)
    final = work / "Fallout4VoiceRandomizer"
    if final.exists():
        raise FileExistsError(f"Output already exists: {final}. Move or rename it before running.")
    progress("Reading archives", 0, 1)
    voice_archives = discover(game, mode="character" if mode == "chaos" else mode) if mode != "sounds" else []
    sound_archives = discover_sounds(game) if mode in ("sounds", "chaos") else []
    archives = voice_archives + sound_archives
    identities = {}
    for archive in archives:
        stat = archive.path.stat()
        identities[archive.path] = (stat.st_size, stat.st_mtime_ns)
    by_path: dict[str, Input] = {}
    pools: dict[str, list[str]] = {}
    formats: dict[str, SoundFormat] = {}
    exclusions = []
    if mode in ("sounds", "chaos"):
        candidates: dict[str, list[Input]] = {}
        for archive in sound_archives:
            for entry in archive.entries:
                candidates.setdefault(entry.path.casefold(), []).append(Input(archive, entry, sound=True))
        total_candidates = len(candidates)
        for index, (key, items) in enumerate(sorted(candidates.items()), 1):
            if cancelled():
                raise Cancelled()
            item = items[0]
            reason = None
            if len(items) > 1:
                reason = "Duplicate path"
            elif item.archive.path.name.casefold() == "fallout4 - startup.ba2":
                reason = "Startup loose-file override is not verified"
            elif ("music" in key or "jingle" in key
                  or "mus" in re.split(r"[/_.-]+", key)):
                reason = "Music-related path"
            elif Path(item.entry.path).suffix.casefold() not in (".wav", ".xwm"):
                reason = "Unsupported file extension"
            else:
                try:
                    fmt = sound_format(item.archive.read(item.entry))
                    if fmt.kind != Path(item.entry.path).suffix[1:].casefold():
                        raise ValueError("Audio format does not match the file extension.")
                except UnsupportedSound as error:
                    reason = str(error)
                except ValueError as error:
                    raise ValueError(
                        f"Invalid sound {item.archive.path.name}!{item.entry.path}: {error}"
                    ) from error
                else:
                    by_path[key] = item
                    formats[key] = fmt
                    pools.setdefault("chaos" if mode == "chaos" else "sound-effects", []).append(item.entry.path)
            if reason:
                exclusions.append({"path": item.entry.path,
                                   "archives": [i.archive.path.name for i in items],
                                   "reason": reason})
            if index % 100 == 0 or index == total_candidates:
                progress("Inspecting sounds", index, total_candidates)
    if mode != "sounds":
        seen_voices: set[str] = set()
        total_voices = sum(len(archive.entries) for archive in voice_archives)
        voice_index = 0
        for archive in voice_archives:
            for entry in archive.entries:
                if mode == "chaos" and cancelled():
                    raise Cancelled()
                key = entry.path.casefold()
                if key in seen_voices or key in by_path:
                    raise ValueError(f"Duplicate voice path across archives: {entry.path}")
                seen_voices.add(key)
                if mode == "chaos":
                    try:
                        fmt = sound_format(Fuz.parse(archive.read(entry)).audio)
                    except UnsupportedSound as error:
                        exclusions.append({"path": entry.path, "archives": [archive.path.name],
                                           "reason": str(error)})
                    except ValueError as error:
                        raise ValueError(f"Invalid voice {archive.path.name}!{entry.path}: {error}") from error
                    else:
                        by_path[key] = Input(archive, entry)
                        formats[key] = fmt
                        pools.setdefault("chaos", []).append(entry.path)
                    voice_index += 1
                    if voice_index % 100 == 0 or voice_index == total_voices:
                        progress("Inspecting voices", voice_index, total_voices)
                    continue
                by_path[key] = Input(archive, entry)
                if mode == "character":
                    pool_key = entry.path.rsplit("/", 1)[0].casefold()
                else:
                    pool_key = entry.voice
                pools.setdefault(pool_key, []).append(entry.path)
    if mode == "chaos" and not any(item.sound for item in by_path.values()):
        raise ValueError("No supported sound effects were found for chaos mode.")
    if mode == "chaos" and not any(not item.sound for item in by_path.values()):
        raise ValueError("No supported voice lines were found for chaos mode.")
    if not by_path:
        if mode == "sounds":
            raise ValueError("No supported sound effects were found.")
        elif mode == "character":
            raise ValueError("No voice lines were found.")
        else:
            raise ValueError("No player voice lines were found.")
    mapping = {}
    for pool_key, paths in sorted(pools.items()):
        voice_seed = seed + ":" + pool_key
        mapping.update(shuffle_pool(paths, voice_seed))
    required = sum(item.entry.size for item in by_path.values()) * (
        2 if output_type == "ba2" or mode in ("sounds", "chaos") else 1
    )
    if shutil.disk_usage(work).free < required:
        raise OSError("Not enough free space in the working folder.")
    if cancelled():
        raise Cancelled()

    with tempfile.TemporaryDirectory(prefix=".voice-randomizer-", dir=work) as temporary:
        stage = Path(temporary)
        mod = stage / "mod"
        mod.mkdir()
        files = stage / "files" if output_type == "ba2" else mod
        manifest, records = _write_entries(mapping, by_path, formats, stage, files, work,
                                           progress, cancelled, mode=mode)
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
        if mode in ("sounds", "chaos"):
            report.update({"mode": mode, "pool_rules": (
                               "Supported voice audio and WAV and XWM sound effects"
                               if mode == "chaos" else "Supported WAV and XWM sound effects"),
                           "exclusions": exclusions,
                           "unchanged_pools": sum(len(paths) == 1 for paths in pools.values())})
        (stage / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        log_lines = [
            f"Fallout 4 Voice Randomizer {__version__} mapping",
            f"Seed: {seed}",
            "",
        ]
        log_lines.extend(
            f"{item['source_archive']}!{item['source']} -> "
            f"{item['destination_archive']}!{item['destination']}"
            + (f" ({item['source_type']} {item['source_format']} -> "
               f"{item['destination_type']} {item['destination_format']})"
               if mode == "chaos" else "")
            for item in manifest
        )
        if mode in ("sounds", "chaos"):
            log_lines.extend(["", f"Excluded {'audio' if mode == 'chaos' else 'sound'} paths: {len(exclusions)}"])
            log_lines.extend(
                f"Excluded {item['path']} ({', '.join(item['archives'])}): {item['reason']}"
                for item in exclusions
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
            report_size = sum((stage / name).stat().st_size
                              for name in ("manifest.json", "randomization_log.txt"))
            if shutil.disk_usage(work).free < report_size + 2 * zip_estimate:
                raise OSError("Not enough free space to create and publish the ZIP and reports.")
            _write_zip(mod, stage / "Fallout4VoiceRandomizer.zip", progress, cancelled)
            if cancelled():
                raise Cancelled()
            shutil.rmtree(mod)
        # Publish only after validation; remove partial output if copying fails.
        if cancelled():
            raise Cancelled()
        _publish(stage, final, progress, cancelled)
    return final
