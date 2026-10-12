"""The sender's installed-games functions, against a fake console folder tree."""
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import pw_quick  # noqa: E402

ROOT = pw_quick.REMOTE_ROOT


class FakeConsole:
    """Folders and files in a dict; paths are absolute, as on the console."""

    def __init__(self, files: dict[str, bytes]):
        self.tree: dict[str, tuple[str, bytes | None]] = {"/": ("dir", None)}
        for path, data in files.items():
            parts = path.strip("/").split("/")
            for index in range(1, len(parts)):
                self.tree.setdefault("/" + "/".join(parts[:index]), ("dir", None))
            self.tree["/" + "/".join(parts)] = ("file", data)

    def _children(self, path: str) -> dict[str, tuple[str, int]]:
        prefix = path.rstrip("/") + "/"
        out = {}
        for key, (kind, data) in self.tree.items():
            if key.startswith(prefix) and "/" not in key[len(prefix):] and key != path:
                out[key[len(prefix):]] = (kind, len(data or b""))
        return out

    def listdir(self, path):
        if self.tree.get(path, ("", None))[0] != "dir":
            raise OSError(path)
        return self._children(path)

    def exists(self, path):
        return path in self.tree

    def size(self, path):
        entry = self.tree.get(path)
        return len(entry[1]) if entry and entry[0] == "file" else None

    def read(self, path):
        return self.tree[path][1]

    def write(self, path, data):
        self.tree[path] = ("file", data)

    def delete(self, path):
        if self.tree.get(path, ("", None))[0] != "file":
            raise OSError(path)
        del self.tree[path]

    def rmdir(self, path):
        if self._children(path):
            raise OSError("not empty: " + path)
        del self.tree[path]


PROFILE = "name = {name}\nexecutable = C:\\Games\\{slug}\\game.exe\n".encode()


def console_with_games() -> FakeConsole:
    return FakeConsole({
        f"{ROOT}/profiles/zeta.profile": PROFILE.replace(b"{name}", b"Zeta").replace(b"{slug}", b"zeta"),
        f"{ROOT}/profiles/alpha.profile": PROFILE.replace(b"{name}", b"alpha game").replace(b"{slug}", b"alpha"),
        f"{ROOT}/profiles/profiles.lst": b"alpha.profile\nzeta.profile\n",
        f"{ROOT}/prefixes/alpha/drive_c/Games/alpha/game.exe": b"exe",
        f"{ROOT}/prefixes/alpha/drive_c/users/prospero/AppData/Roaming/Alpha/save1.dat": b"save one",
        f"{ROOT}/prefixes/alpha/drive_c/users/prospero/AppData/Roaming/Alpha/deep/save2.dat": b"save two",
        f"{ROOT}/prefixes/alpha/drive_c/users/prospero/Documents/notes.txt": b"notes",
        f"{ROOT}/prefixes/alpha/drive_c/windows/system32/ntdll.dll": b"dll",
        f"{ROOT}/prefixes/zeta/drive_c/Games/zeta/game.exe": b"zeta exe",
    })


def test_installed_games_sorted_by_name():
    console = console_with_games()
    assert pw_quick.installed_games(console) == [("alpha", "alpha game"), ("zeta", "Zeta")]


def test_installed_games_empty_without_profiles():
    assert pw_quick.installed_games(FakeConsole({})) == []


def test_export_saves_zips_only_the_user_folders():
    console = console_with_games()
    with tempfile.TemporaryDirectory() as folder:
        out, count = pw_quick.export_saves(console, "alpha", folder, "stamp")
        assert count == 3, count
        assert out.name == "alpha-saves-stamp.zip"
        with zipfile.ZipFile(out) as archive:
            assert sorted(archive.namelist()) == [
                "AppData/Roaming/Alpha/deep/save2.dat",
                "AppData/Roaming/Alpha/save1.dat",
                "Documents/notes.txt",
            ], archive.namelist()
            assert archive.read("AppData/Roaming/Alpha/save1.dat") == b"save one"


def test_export_saves_refuses_a_game_with_no_saves():
    console = console_with_games()
    with tempfile.TemporaryDirectory() as folder:
        try:
            pw_quick.export_saves(console, "zeta", folder, "stamp")
        except pw_quick.QuickError as error:
            assert "no save files" in str(error), error
        else:
            raise AssertionError("expected QuickError")


def test_export_saves_refuses_an_unknown_game():
    with tempfile.TemporaryDirectory() as folder:
        try:
            pw_quick.export_saves(FakeConsole({}), "nope", folder, "stamp")
        except pw_quick.QuickError as error:
            assert "no prefix" in str(error), error
        else:
            raise AssertionError("expected QuickError")


def test_delete_game_removes_prefix_profile_and_list_line():
    console = console_with_games()
    with tempfile.TemporaryDirectory() as state:
        record = Path(state) / "console-alpha.json"
        record.write_text("{}")
        pw_quick.delete_game(console, "alpha", host="console", state_dir=state)
        assert not record.exists()
        assert not any(key.startswith(f"{ROOT}/prefixes/alpha") for key in console.tree)
        assert f"{ROOT}/profiles/alpha.profile" not in console.tree
        assert console.read(f"{ROOT}/profiles/profiles.lst") == b"zeta.profile\n"
        # The other game is untouched.
        assert f"{ROOT}/prefixes/zeta/drive_c/Games/zeta/game.exe" in console.tree
        assert f"{ROOT}/profiles/zeta.profile" in console.tree


def test_delete_game_empties_the_list_when_it_was_the_last_game():
    console = FakeConsole({
        f"{ROOT}/profiles/only.profile": PROFILE.replace(b"{name}", b"Only").replace(b"{slug}", b"only"),
        f"{ROOT}/profiles/profiles.lst": b"only.profile\n",
    })
    with tempfile.TemporaryDirectory() as state:
        pw_quick.delete_game(console, "only", state_dir=state)
    assert console.read(f"{ROOT}/profiles/profiles.lst") == b""


def test_delete_game_when_nothing_is_left():
    with tempfile.TemporaryDirectory() as state:
        pw_quick.delete_game(FakeConsole({}), "ghost", state_dir=state)


def test_parse_lanes_accepts_whole_numbers_in_range():
    assert pw_quick.parse_lanes("4") == 4
    assert pw_quick.parse_lanes(" 1 ") == 1
    assert pw_quick.parse_lanes(8) == 8


def test_parse_lanes_refuses_out_of_range_and_non_numbers():
    for bad in ("0", "9", "-2", "2.5", "four", ""):
        try:
            pw_quick.parse_lanes(bad)
        except pw_quick.QuickError:
            continue
        raise AssertionError(f"{bad!r} should be refused")
