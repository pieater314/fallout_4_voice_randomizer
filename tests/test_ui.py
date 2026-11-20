import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import tkinter as tk
    from tkinter import ttk
except ModuleNotFoundError:
    tk = None


@unittest.skipIf(tk is None, "Tkinter is not installed")
class ModeSelectionTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display is unavailable: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        after = patch.object(self.root, "after")
        after.start()
        self.addCleanup(after.stop)
        self.addCleanup(self.root.update_idletasks)
        from voice_randomizer.__main__ import Application

        self.app = Application(self.root)

    def test_version_stays_at_bottom_right_when_window_is_resized(self):
        from voice_randomizer import __version__

        self.assertEqual(self.root.title(), "Fallout 4 Voice Randomizer")
        frame = self.app.instructions_frame.master
        labels = [
            widget for widget in frame.winfo_children()
            if isinstance(widget, ttk.Label) and widget["text"] == f"v{__version__}"
        ]
        self.assertEqual(len(labels), 1)
        label = labels[0]
        self.root.deiconify()
        for size in ("1000x900", "1200x1100"):
            with self.subTest(size=size):
                self.root.geometry(size)
                self.root.update()
                right_gap = self.root.winfo_rootx() + self.root.winfo_width() - (
                    label.winfo_rootx() + label.winfo_width()
                )
                bottom_gap = self.root.winfo_rooty() + self.root.winfo_height() - (
                    label.winfo_rooty() + label.winfo_height()
                )
                self.assertEqual(right_gap, 16)
                self.assertEqual(bottom_gap, 16)

    def test_only_main_character_mode_can_be_selected(self):
        def widgets(parent):
            for child in parent.winfo_children():
                yield child
                yield from widgets(child)

        buttons = {
            button["value"]: button
            for button in widgets(self.root)
            if isinstance(button, ttk.Radiobutton)
            and str(button["variable"]) == str(self.app.mode)
        }
        self.assertEqual(set(buttons), {"character", "player", "sounds", "chaos", "austin"})
        self.assertTrue(all("(not available)" not in button["text"] for button in buttons.values()))
        self.assertEqual(self.app.mode.get(), "player")
        self.assertFalse(buttons["player"].instate(["disabled"]))
        self.assertFalse(buttons["character"].instate(["disabled"]))
        for name in ("sounds", "chaos", "austin"):
            with self.subTest(mode=name):
                self.assertTrue(buttons[name].instate(["disabled"]))

    def test_ba2_output_is_unavailable(self):
        def widgets(parent):
            for child in parent.winfo_children():
                yield child
                yield from widgets(child)

        buttons = {
            button["value"]: button
            for button in widgets(self.root)
            if isinstance(button, ttk.Radiobutton)
            and str(button["variable"]) == str(self.app.output)
        }
        self.assertEqual(self.app.output.get(), "loose")
        self.assertFalse(buttons["loose"].instate(["disabled"]))
        self.assertTrue(buttons["ba2"].instate(["disabled"]))
        buttons["ba2"].invoke()
        self.assertEqual(self.app.output.get(), "loose")

    def test_completion_instructions_appear_in_main_window_for_built_output_type(self):
        for output_type, create_zip in (
            ("loose", False), ("loose", True), ("ba2", False), ("ba2", True)
        ):
            with self.subTest(output_type=output_type, create_zip=create_zip):
                self.app.output.set("ba2" if output_type == "loose" else "loose")
                self.app.create_zip.set(not create_zip)
                self.app.events.put(("done", r"C:\test\output", output_type, create_zip))
                with patch("voice_randomizer.__main__.messagebox.showinfo") as showinfo:
                    self.app.poll()
                self.assertIn("main window", showinfo.call_args.args[1])
                self.assertEqual(self.app.status.get(), "Mod created.")
                self.assertEqual(self.app.instructions_frame.grid_info()["row"], 8)
                message = self.app.install_instructions.get("1.0", "end-1c")
                self.assertIn(r"C:\test\output", message)
                if create_zip:
                    self.assertIn("Fallout4VoiceRandomizer.zip", message)
                    self.assertNotIn("Zip the contents", message)
                else:
                    self.assertIn("mod folder", message)
                    self.assertIn("Zip the contents", message)
                self.assertIn("mod manager", message)
                if output_type == "loose":
                    self.assertIn("archive invalidation", message)
                    self.assertIn("bInvalidateOlderFiles=1", message)
                    self.assertNotIn("ESL plugins", message)
                else:
                    self.assertIn("Enable all generated ESL plugins", message)
                    self.assertIn("not been tested in game", message)
                    self.assertNotIn("archive invalidation", message)

    def test_instructions_allow_selection_without_editing(self):
        self.app._set_install_instructions("bInvalidateOlderFiles=1\nsResourceDataDirsFinal=")
        text = self.app.install_instructions
        text.tag_add("sel", "1.0", "end-1c")
        self.assertEqual(
            text.get("sel.first", "sel.last"),
            "bInvalidateOlderFiles=1\nsResourceDataDirsFinal=",
        )
        text.insert("end", "unwanted")
        self.assertNotIn("unwanted", text.get("1.0", "end-1c"))

    def test_starting_run_clears_previous_instructions(self):
        self.assertEqual(self.app.instructions_frame.grid_info()["row"], 8)
        self.assertIn("will appear here", self.app.install_instructions.get("1.0", "end-1c"))
        self.app._set_install_instructions("Old instructions")
        with patch("voice_randomizer.__main__.threading.Thread"):
            self.app.start_run()
        self.assertIn("will appear here", self.app.install_instructions.get("1.0", "end-1c"))
        self.assertEqual(self.app.instructions_frame.grid_info()["row"], 8)

    def test_zip_selection_is_passed_to_build(self):
        self.assertTrue(self.app.create_zip.get())
        with patch("voice_randomizer.__main__.threading.Thread") as thread, patch(
            "voice_randomizer.__main__.build", return_value=Path("output")
        ) as build:
            self.app.start_run()
            self.app.create_zip.set(False)
            thread.call_args.kwargs["target"]()
        self.assertTrue(build.call_args.kwargs["create_zip"])
        self.assertEqual(self.app.events.get_nowait()[-1], True)

    def test_character_selection_is_passed_to_build(self):
        self.app.mode.set("character")
        with patch("voice_randomizer.__main__.threading.Thread") as thread, patch(
            "voice_randomizer.__main__.build", return_value=Path("output")
        ) as build:
            self.app.start_run()
            thread.call_args.kwargs["target"]()
        self.assertEqual(build.call_args.kwargs["mode"], "character")


class DefaultPathTests(unittest.TestCase):
    def test_game_path_defaults_to_standard_steam_install(self):
        from voice_randomizer.__main__ import _default_game_path

        with patch.dict("os.environ", {"ProgramFiles(x86)": r"D:\Programs"}):
            self.assertEqual(
                _default_game_path(),
                Path(r"D:\Programs\Steam\steamapps\common\Fallout 4"),
            )

    def test_work_path_uses_executable_folder_when_frozen(self):
        from voice_randomizer import __main__ as app

        with patch.object(app.sys, "frozen", True, create=True), patch.object(
            app.sys, "executable", r"D:\Apps\VoiceRandomizer.exe"
        ):
            self.assertEqual(app._default_work_path(), Path(r"D:\Apps"))

    def test_work_path_uses_current_directory_when_running_python(self):
        from voice_randomizer import __main__ as app

        with patch.object(app.sys, "frozen", False, create=True), patch.object(
            Path, "cwd", return_value=Path(r"D:\Projects\VoiceRandomizer")
        ):
            self.assertEqual(app._default_work_path(), Path(r"D:\Projects\VoiceRandomizer"))
