# WMAEncodeRS bundled executable

- Version: v0.5.2
- Release: https://github.com/MSIVST/WMAEncodeRS/releases/tag/v0.5.2
- Platform: x86-64 Windows
- SHA-256: `865cb751a48a14f0b253cfdd0b258ed86925791e57c2a1bc542107522af8c0ca`
- License: see [LICENSE](./LICENSE)

The executable uses the Windows Media runtime included with standard Windows
10 and 11 installations. Windows N/KN editions may need the Media Feature
Pack. Keep this executable and its license in this folder when packaging the
application. The audio conversion code checks this folder automatically in a
source checkout and in a PyInstaller bundle that places `vendor/` at its
bundle root.
