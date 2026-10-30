# Third-party notices and corresponding source

AutoDL Fresh 0.7.8 uses independently licensed third-party software. The project's
non-commercial license applies only to the copyright holder's original work.
It does **not** replace, narrow, or claim ownership over any third-party license.

## Qt, Qt for Python and Shiboken — 6.10.2

This application uses Qt and Qt for Python (PySide6 / Shiboken) under the
**GNU Lesser General Public License, version 3 (LGPLv3)** option. Qt is copyright
The Qt Company Ltd. and its contributors. PySide6 and Shiboken retain their
respective upstream copyright notices.

The Windows app uses dynamically loaded libraries in `_internal/PySide6` and
`_internal/shiboken6`. They have not been modified by this project. Optional PDF,
virtual-keyboard and QML/Quick plugins are excluded from the distributed app.
No commercial Qt license is represented as having been acquired.

Full license texts, including the GPLv3 text incorporated by LGPLv3 and embedded
third-party attributions, are included under `licenses/third-party`.

The [v0.7.8 release](https://github.com/wanfeng562-code/autodl-fresh/releases/tag/v0.7.8)
offers **AutoDLFresh-0.7.8-third-party-sources.zip**, containing the complete,
unmodified source archives for the Qt components shipped by this application:

- [Qt Base 6.10.2](https://download.qt.io/official_releases/qt/6.10/6.10.2/submodules/qtbase-everywhere-src-6.10.2.tar.xz)
- [Qt SVG 6.10.2](https://download.qt.io/official_releases/qt/6.10/6.10.2/submodules/qtsvg-everywhere-src-6.10.2.tar.xz)
- [Qt Image Formats 6.10.2](https://download.qt.io/official_releases/qt/6.10/6.10.2/submodules/qtimageformats-everywhere-src-6.10.2.tar.xz)
- [Qt for Python / Shiboken 6.10.2](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.10.2-src/pyside-setup-everywhere-src-6.10.2.tar.xz)

Archive URLs and SHA-256 hashes are recorded in `licenses/third-party/sources.json`.
The original build/configuration scripts and third-party notices are included in
those upstream archives. Upstream build instructions:
[Qt](https://doc.qt.io/qt-6/build-sources.html),
[Qt for Python](https://doc.qt.io/qtforpython-6/building_from_source/index.html).

Users may modify, replace, and debug the LGPL libraries, including reverse
engineering the combined work for that purpose, and recombine or relink this
application with modified compatible libraries. No signature or integrity gate
prevents library replacement. After backing up the app, replace the corresponding
ABI-compatible DLL/PYD and plugin files in `_internal`, or run the published app
source with your modified PySide6 installation and rebuild using `build.ps1`.
The source repository contains the corresponding application code and build
configuration. See the LICENSE exception protecting these third-party rights.

## Other components

| Component | Version in 0.7.8 | Upstream license / notices |
| --- | --- | --- |
| CPython runtime | 3.12.4 | Python Software Foundation license and bundled third-party notices |
| Playwright for Python / driver | 1.64.1 | Apache-2.0; upstream NOTICE and ThirdPartyNotices.txt |
| Node.js in Playwright driver | version supplied by the Playwright distribution | Node.js LICENSE with embedded third-party notices |
| pyee | 14.0.0 | MIT |
| greenlet | 3.5.6 | MIT and Python-derived license notices |
| typing_extensions | 4.12.2 | PSF-2.0 |
| PyInstaller bootloader | 6.19.0 | GPL-2.0-or-later with the upstream bootloader exception |

Their notice and license files are included in `licenses/third-party` and, for the
Playwright driver, inside `_internal/playwright/driver`. Qt's embedded libraries
(including image codecs and the software OpenGL renderer) retain their upstream
licenses and notices. Operating-system components and Microsoft redistributable
runtime DLLs retain their respective Microsoft terms and ownership.

Microsoft Edge / Google Chrome are **not** redistributed. The app uses a browser
already installed by the user. AutoDL Fresh is not an official AutoDL product and
does not claim AutoDL's trademarks or any relationship with AutoDL.

If a corresponding-source download is unavailable, report the missing asset at
https://github.com/wanfeng562-code/autodl-fresh/issues so the publisher can restore it.
