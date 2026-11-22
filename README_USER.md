# Fallout 4 Voice Randomizer

This guide explains how to use this application.

## Usage

The application supports the following modes:

- **Within each character/NPC** shuffles voice lines within each character or
  NPC.
- **Main character voices** shuffles the main character voice lines.
- **Sounds only** shuffles WAV and XWM effects under `sound/fx`. It converts
  audio to the destination format when needed. It excludes voice files and
  paths marked as music or jingles. This name-based filter cannot identify all
  music. Some effects can contain speech or music.

The application currently creates loose files and/or a ZIP file. BA2 output is not available.

## Before you start

- Use the application on Windows 10 or 11.
- Keep all files in the `Fallout4VoiceRandomizer` folder together.
- The game folder must contain `Fallout4.exe` and a `Data` folder. 
  The default game files location is the default Steam install location.
- Voice modes need the base game voice archive. Sounds-only mode needs the
  base game sound archive. 
- The app uses recognized official DLC when
  its plugin and matching archive are present.
- The bundled audio converter needs the Windows Media runtime. Windows N/KN
  editions may need the Media Feature Pack.

## Create a mod for use in Vortex, Mod Oraganizer 2, or other modding tools

1. Run `Fallout4VoiceRandomizer.exe`.
2. Select your Fallout 4 folder. The default is the standard Steam install
   folder. Use **Browse** if your game is in a different location.
3. Select a working folder. The default is the folder that contains the
   application. Select a folder outside the game installation. Do not select
   the game folder or one of its parent folders.
4. Select a randomization mode.
5. Under **Mod output**, keep **Loose files** selected. BA2 archive files are
   not available.
6. **Create ZIP for mod manager (recommended)** is selected by default. Keep
   it selected to create `Fallout4VoiceRandomizer.zip` in the output folder.
   Clear it to leave the files in the `mod` folder for manual packaging.
7. Keep the generated seed, select **New seed** to create another, or enter
   your own. Use the same seed, game files, and app version to repeat a shuffle.
8. Select **Create mod** and wait for the run to finish.

To stop a run, select **Cancel** and wait for the application to stop. If the
application finds an invalid folder, multiple voice archives for one game or
DLC, or duplicate voice paths, it reports an error and does not publish a mod.

## Find the output

The application creates this folder:

`<working folder>\Fallout4VoiceRandomizer`

It contains:

- `mod` - the generated mod files.
- `Fallout4VoiceRandomizer.zip` - present when ZIP creation is selected.
- `manifest.json` - the seed and input archive details.
- `randomization_log.txt` - a list of the file changes and any sound exclusions.

Move or rename an existing `Fallout4VoiceRandomizer` output folder before you
create another mod in the same working folder.

## Install or remove the mod

Install the generated ZIP with your Fallout 4 mod manager, then enable it. If
you did not create a ZIP, zip the contents of the `mod` folder so the `Sound`
folder is at the ZIP's root. The application displays extra steps after it
creates the mod.

Loose-file mods need archive invalidation enabled in your mod manager. You can
also add these lines under `[Archive]` in
`Documents\My Games\Fallout4\Fallout4Custom.ini` or your mod manager's profile
INI:

```ini
[Archive]
bInvalidateOlderFiles=1
sResourceDataDirsFinal=
```

To remove the mod, disable or remove it with your mod manager. Do not replace
or edit the original game archives.
