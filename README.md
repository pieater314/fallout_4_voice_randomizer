# Fallout 4 Voice Randomizer

> [!WARNING]
> This is a beta project. Generated files may not always work.

## Summary

A Windows-only utility that shuffles Fallout 4 player and NPC voice lines into
experimental mod files, with optional ZIP output and a manifest.

## Current limitations

- Sounds-only, chaos, and Austin. modes are not available.
- BA2 output is experimental and disabled in the interface.
- Other Fallout games are not supported.
- Loose-file and BA2 output have not been verified in game. BA2 plugin loading
  and archive precedence have not been verified for every setup.
- Real audio conversion through the app and WAV playback in Fallout 4 remain
  unverified.

The app stops if it finds duplicate selected voice paths or multiple voice
archives for the same base game or DLC. It includes recognized official DLC
only when both the matching `.esm` and voice archive are present.

## Run from source

1. Install 64-bit Python 3.10 or later with Tkinter on Windows.
2. Open PowerShell in the project folder and create a virtual environment:

   ```powershell
   python -m venv .venv
   ```

3. Start the application:

   ```powershell
   .\.venv\Scripts\python.exe -m voice_randomizer
   ```

The application has no third-party Python runtime dependencies. Use a separate
virtual environment on each operating system. Virtual environments are not
portable between Windows and Linux.

### Audio conversion

The official WMAEncodeRS v0.5.2 Windows executable is stored at
`vendor/WMAEncodeRS/WMAEncodeRS.exe`. The application checks this bundled
location automatically. You can override it by setting `WMAENCODERS_PATH`
before starting the application:

```powershell
$env:WMAENCODERS_PATH = "C:\Tools\WMAEncodeRS.exe"
.\.venv\Scripts\python.exe -m voice_randomizer
```

WMAEncodeRS supports XWMA to WAV decoding and WAV to XWMA encoding. The
application validates the output container before it uses the converted audio.
This check does not prove that the audio will play in the game. Real conversion
through this app and WAV playback in Fallout 4 remain unverified. Same-format
replacements do not need conversion. The executable requires
64-bit Windows 10 or 11 and the Windows Media runtime. Windows N/KN editions
may need the Media Feature Pack. Its license and release details are in
[`vendor/WMAEncodeRS/`](vendor/WMAEncodeRS/README.md). Conversion can use
temporary files in the system temporary folder as well as space in the working
folder.

## Build a Windows test app

Build the test app on a 64-bit Windows computer. PyInstaller does not
cross-build Windows apps.

1. Install 64-bit Python 3.10 or later, including Tkinter, `pip`, and the
   `py` launcher. Install the Windows Media runtime. Windows N/KN editions
   may need the Media Feature Pack.
2. Open Command Prompt in the project folder and run `build_windows.bat`.
   The build requires internet access to install PyInstaller and its
   dependencies.
3. Start `dist\Fallout4VoiceRandomizer\Fallout4VoiceRandomizer.exe`.

The build script creates an isolated `.build-venv`, installs the pinned build
tool from `requirements-build.txt`, and creates a one-folder app. The folder
contains the app, WMAEncodeRS, and its license. Keep and copy the full
`dist\Fallout4VoiceRandomizer` folder; do not copy only the `.exe`.
The executable's Windows file properties show the application version under
**Details**. This version comes from `voice_randomizer/__init__.py`.

> [!CAUTION]
> The build script replaces the existing build output without prompting. Back
> up any files you need from that folder before rebuilding.

The PyInstaller spec has produced a Windows test build, but the build script
itself has not been verified. This is a test build, not an installer. It does
not need Python on the test computer, but it does need the Windows Media
runtime and access to a Fallout 4 installation to create a mod.

## Create a mod

1. The game folder defaults to
   `C:\Program Files (x86)\Steam\steamapps\common\Fallout 4`. Change it if
   Fallout 4 is installed elsewhere; the selected folder must contain
   `Fallout4.exe` and `Data`.
2. The working folder defaults to the application's `.exe` directory in the
   packaged build, or the current directory when launched with Python. Change
   it if needed; it must be neither inside the game installation nor a
   parent of it. The application uses temporary staging files. Allow space for
   the generated voice data; BA2 output also stages loose files. Allow extra
   space for conversion and filesystem overhead. ZIP output requires additional
   space for both the ZIP and its staged copy.
3. Select **Main character voices** or **Within each character/NPC**.
4. `Loose files` is selected; BA2 output is currently unavailable in the UI.
5. Leave **Create mod ZIP** selected to generate an installable ZIP alongside
   the mod folder, or clear it to skip ZIP creation.
6. Enter a seed, or keep the generated seed.
7. Select **Create mod**.

Use **Cancel** to request cancellation and wait for the run to stop. Use the
folder buttons to open the selected folders on Windows.

The output is created at
`<working folder>/Fallout4VoiceRandomizer`. To run again, move or rename an
existing output folder first. The `mod` folder contains the generated files;
`manifest.json` records the seed, input archive names, sizes and modification
times, and mapping. `randomization_log.txt` provides a readable
source-archive/source-path to destination-archive/destination-path list. If
selected, `Fallout4VoiceRandomizer.zip` contains only the generated mod files.
The ZIP stores files without compression. Keep generated files, ZIPs, and
manifests local: they can contain game data and must not be distributed with
the application.

## Install and remove the mod

Treat every output as experimental. Keep the original game archives unchanged
and install the generated ZIP with a mod manager. If ZIP creation was not
selected, zip the contents of the `mod` folder yourself, with `sound` (loose
output) or the BA2 and ESL files (BA2 output) at the ZIP's root. Install and
enable the ZIP in the mod manager.

- For loose files, enable the archive-invalidation setting required by the
  target Fallout 4 setup. Alternatively, add the following settings under
  `[Archive]` in `Documents\My Games\Fallout4\Fallout4Custom.ini` (or your
  mod manager's profile INI):

  ```ini
  [Archive]
  bInvalidateOlderFiles=1
  sResourceDataDirsFinal=
  ```
- For BA2 output, install the generated `.ba2` files and `.esl` plugins. BA2
  output is experimental and has not been verified in game.
- Other mods that replace the same voice files can conflict. The active
  replacement depends on the game's and mod manager's load order.
- To remove the result, disable or remove the generated mod from the mod
  manager. Do not replace the original voice archives.

The generated audio keeps the destination lip-sync data. Voice lines can
therefore be out of sync with lip movements or subtitles.

## Tests

Run the test suite from the project folder. On Windows, after creating the
virtual environment:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

The tests use synthetic archive and FUZ data and a stand-in converter. They do
not test the real WMAEncodeRS executable or prove compatibility with every
Fallout 4 archive, audio file, mod manager, or game version.
