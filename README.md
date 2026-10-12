# prospero-win sender: source and how it works

This zip is the source of the sender: the Windows app that takes a PC game
(a zip or a folder) and puts it on a jailbroken PS5 running prospero-win,
with its settings worked out automatically. It is taken from the
`sender-gui` branch of the fork at commit `e8adee0` (v4.27).

Binaries are not included (the sender .exe, the prebuilt base prefix zip,
Wine Mono, `wowprospero.dll`, the D3D12 proxy DLLs, `pwexec*.exe`, the
Steamworks stub DLL). Each one is rebuilt from the source here with the
scripts listed below. Nothing here is a game, key or firmware.

## What is in the zip

| Path | What it is |
|---|---|
| `tools/pw_gui.py` | The Windows window (Python Tk). Packaged into the .exe. |
| `tools/pw_quick.py` | The core: finds the game's program, works out its settings, builds its prefix, sends it. Also has a command-line mode. |
| `tools/pw_autoconfig.py` | Engine detection (Unity, Unreal, Godot, GameMaker, RPG Maker, Ren'Py, LÖVE, Electron, .NET, XNA, Java) and the per-engine rules for exe, graphics and arguments. |
| `tools/pw_prefix.py` | Talks to the console over FTP, and the prefix/profile file format it uses. |
| `tools/pw_install.py` | Installs games on a Linux PC with the pinned Wine. The sender uses only its pinned DXVK release lookup and download (`dxvk_release`, `fetch`); the rest is for the library side. |
| `tools/pw_base_prefix.py` | Makes the base prefix zip (a Wine prefix made once with the pinned host Wine). |
| `tools/presets/` | Controller presets (`*.input`): mouse, gamepad, warcraft3, goldsrc, pinball. Copied to the console by name. |
| `tools/d3d12_proxy/` | Source of the Direct3D 12 proxy `d3d12.dll` and `d3d12core` stand-ins (feature level capped at 11_0, reports 16 GB RAM). |
| `tools/pwexec/` | `pwexec.c`, the in-process launcher loader (built as `pwexec32.exe` and `pwexec64.exe`). |
| `wine/wowprospero/` | The WoW64 CPU backend source (`cpu.c`, `legacy_ops.h`, ...), built to `wowprospero.dll`. `legacy_ops.h` emulates the x86 instructions the console's 32-bit translator refuses. |
| `tools/build_*.sh`, `tools/media/` | Build scripts for the pieces the sender bundles: host Wine, the media (FFmpeg-backed Media Foundation) layer, Wine Mono, the Steamworks stub, the CPU backend. |
| `tools/sender-licenses/` | License texts and `NOTICES.txt` for everything bundled. |
| `tests/test_pw_quick.py`, `test_pw_autoconfig.py`, `test_pw_prefix.py`, `test_pw_install.py` | The Python tests for the sender. Run: `python3 -m pytest -q tests/test_pw_quick.py tests/test_pw_autoconfig.py tests/test_pw_prefix.py` |
| `docs/SENDING_GAMES.md`, `docs/VIDEO_DECODING.md` | User docs for sending games, and the notes on video decoding on the console. |
| `.github/workflows/sender.yml` | CI: runs the tests, builds the Windows .exe with PyInstaller. |

## The big picture

The console cannot do the hard parts of a Windows install. It cannot start a
second process, and `wineboot` (which makes a Wine prefix) is off there. So
the sender does the work on the PC and sends the result:

1. A **base prefix** is made once on a PC with the pinned Wine and shipped as
   a zip. It holds only Wine's own files: `system32`, the registry, the
   stand-in fonts, and the media decoders. It is the same for every game.
2. For each game, the sender copies the game's files into the console's copy
   of that base prefix, at `C:\Games\<slug>`, and writes a profile
   (`profiles/<slug>.profile`) that tells the console app what to run.
3. Anything the game needs beyond the game's own files (DXVK, the D3D12
   proxy, Wine Mono, the translator DLL, the loader, registry entries) is
   added beside the game or in the prefix as "stand-ins".
4. Everything goes over FTP to the console (4 connections, `FTP_LANES`), and
   a record of each file already sent lets an interrupted send resume.

The whole sender is a pipeline of these stages. Each one is a function you
can read on its own.

### Stage 1: read the game (`Source`, `pe_info`, `find_executables`)

`Source` reads a zip or a folder without unpacking it. `pe_info` reads a PE
header and its import table, which says the architecture (32 or 64 bit),
whether the program is a GUI, and which DLLs it imports. `find_executables`
lists every `.exe`, scores it (bigger is likelier, top folder is likelier, a
name that matches the game counts for more, a name like `setup`, `crash`,
`patch` counts against it, a program that imports a graphics library counts
for it), and sorts them, best first.

### Stage 2: work out the settings (`autoconfigure`, `guess_graphics`, `launch_pair`, `configured`)

- **Engine**: `pw_autoconfig.py` looks for each engine's files (Unity's
  `UnityPlayer.dll`, Godot's `.pck`, RPG Maker's `Game.rgss`, and so on) and
  returns a `Plan`: which exe to start, which graphics mode, which arguments
  and environment variables. Unreal starts the shipping exe, not the
  launcher. Godot Compatibility games get Vulkan.
- **Graphics**: `guess_graphics` reads the imports of the game's exe and of
  the game's own DLLs (engines load their renderers from DLLs). Direct3D 8
  to 11 means DXVK; OpenGL means `opengl`; otherwise `gdi`. A game that only
  imports Direct3D 12 gets the vkd3d-proton route (`d3d12`), with the proxy.
- **Launcher pairs**: some games are started through a launcher that hands
  off to the real game (Red Alert 2's `RA2.exe` starts `Game.exe`). The
  console cannot start the second program, so `launch_pair` finds the
  (game, launcher) pair in the same folder. The program that imports a
  graphics library is the game. A sibling that draws nothing, is windowed,
  has the same architecture, and is not a tool (setup, patcher, crash
  handler) is the launcher. Either one may be the exe the user picked. When
  a launcher is found, the profile starts `pwexec` with the launcher as its
  first argument.
- **Game**: `configured` assembles a `Game` (a dataclass) from all this, plus
  the environment that `suggest_environment` reads from the game folder.
  `Game.check()` validates it, and `Game.profile()` writes the profile text.

### Stage 3: build the prefix contents (`Sender.items`, `stand_ins`, `environment`, `reg_entries`)

- `Sender.items` lists the game's files as `Item`s, skipping the junk that
  the Windows file system adds (`__MACOSX`, `.DS_Store`, `Thumbs.db`).
- `stand_ins` returns the extra files the game needs:
  - Direct3D 12 games: the proxy `d3d12.dll` and `d3d12core.dll`, beside the
    exe, plus the vkd3d-proton DLLs.
  - Launcher games: `pwexec32.exe` or `pwexec64.exe`, beside the launcher.
  - Steamworks.NET stub, when the exe names it and the folder lacks it (a
    GOG Stardew Valley build needs it to start under Mono).
  - The translator DLL `wowprospero.dll`, which is bundled, unless the
    console's own app copy is used (`fetch_app_cpu_dll`).
- `reg_entries` reads any `.reg` file in the game folder (for example an
  exported registry key with a CD key). `apply_reg` merges it into the
  prefix's `system.reg` and `user.reg` (for 32-bit games it also copies to
  `Wow6432Node`). The format conversion is in `parse_reg`, `merge_reg`,
  and `reg_text`.
- `environment` builds the Wine environment (for example
  `PW_FAKE_RAM_GB=16` for the D3D12 route) and writes it into `user.reg`.

### Stage 4: send (`Sender.send`, `send_files`, `update_settings`)

- `Sender.send` makes the game's prefix folder on the console (a copy of the
  base prefix, plus the game files) and sends the files. It skips a file
  that already exists at the same size. The state record lets a resend skip
  finished files, and an interrupted send carries on from where it stopped.
  If the game is already on the console, the GUI asks whether to replace it
  (send everything, keeping saves) or to update settings only.
- `send_files` runs the file transfer across the FTP lanes (`each`, `lane`).
  `retrying` reconnects and retries a step (six tries) when the connection
  drops. `CountingReader` and `Progress` feed the speed and the progress bar.
- `update_settings` is the quick path: it rewrites the profile and the
  registry entries, the environment, the stand-ins and the controller preset
  in seconds, and keeps the game's files and saves. Use it after changing a
  setting, or to pick up a new `wowprospero.dll` or loader for a game that
  is already sent.
- `put_mono` sends Wine Mono once to `/data/prospero-win/shared`, for .NET
  Framework and XNA games. `put_preset` copies a controller preset by name
  when the console lacks it. `put_profile` writes the profile.

### Stage 5: the window (`pw_gui.py`)

- The window runs `pw_quick` on a background thread and passes progress and
  status back through a queue (`self.events`), so the window stays
  responsive. A Cancel button stops the transfer; the next Send carries on.
- It fills its fields from `pw_quick.suggest` and `pw_quick.configured`
  when a game is chosen, and lets the user change the exe, the graphics
  mode, the arguments, the environment, the controller preset, and the
  desktop size.
- Other buttons: install or update the prospero-win app from a release zip
  (`install_app`), the Payloads box (sends `.elf` or `.bin` files to the
  console's port 9021 in order, `send_payloads`), and Get logs
  (`fetch_logs` zips `/data/prospero-win/logs` and the profiles beside the
  .exe).
- The `π` in the corner is a small easter egg: Ctrl+Shift+left-click opens
  a link in the default browser.

### Stage 6: the console side (not in this zip)

The sender writes files the console app reads. The app itself, prospero-win
(upstream `mpereiraesaa/prospero-win`, tag v0.1.1), runs Wine on the PS5 with
RADV Vulkan and DXVK, and has an IA-32 translator for 32-bit code. The
sender's replacement translator (`wine/wowprospero`) and loader (`pwexec`) are
built against app 0.1.1. If the app is updated, check them again.

## Build the pieces

The sender .exe is PyInstaller on Windows:

    pyinstaller --noconfirm --onefile --windowed --name prospero-win-sender \
      --paths tools --exclude-module yaml \
      --add-data "tools/presets;presets" --add-data "tools/stubs;stubs" \
      --add-data "tools/d3d12;d3d12" --add-data "tools/cpu;cpu" \
      --add-data "tools/pwexec;pwexec" tools/pw_gui.py

Those `--add-data` folders hold the built binaries:

- `tools/stubs/Steamworks.NET.dll`: `tools/build_steamworks.sh`
- `tools/d3d12/`: `tools/d3d12_proxy/build.sh` (plus vkd3d-proton 3.0.1 DLLs)
- `tools/cpu/wowprospero.dll`: `tools/build_wowprospero.sh`
- `tools/pwexec/pwexec{32,64}.exe`: `tools/pwexec/build.sh` (MinGW)
- Wine Mono zip: `tools/build_mono.sh`
- Base prefix zip: `tools/pw_base_prefix.py` with `tools/build_host_wine.sh`
  (and `tools/build_media.sh` for the media decoders)

## Running the tests

    python3 -m pytest -q tests/test_pw_quick.py tests/test_pw_autoconfig.py \
      tests/test_pw_prefix.py tests/test_pw_install.py

The tests cover the settings rules (engine detection, graphics choice,
launcher pairing), the registry merge, the profile text, and the send
bookkeeping. They don't need a console. Real games have to be checked on the
PS5.

## Known limits

- The console has about 546 MB of RAM reported, and single reservations above
  about 4 GB fail. D3D12 games report 16 GB through the proxy.
- Windows fonts are stand-ins (Arial, Times New Roman, Courier New, MS Gothic).
- MIDI and DirectInput gamepads don't work on the console.
- The 32-bit translator covers every x86 instruction the app's translator
  refuses except 3DNow! and 16-bit addressing.
- A game whose exe has no relocation data and needs a fixed address may fail
  in the loader. Relocatable games work.
- The sender targets prospero-win app 0.1.1. A different app version may
  break the translator DLL and the loader.

## Licenses

Everything the sender bundles is open source. `tools/sender-licenses/NOTICES.txt`
lists each piece and where its source is. The sender itself is LGPL-2.1-or-later.
