import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
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

    def set_valid_folders(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        game = root / "game"
        (game / "Data").mkdir(parents=True)
        (game / "Fallout4.exe").touch()
        work = root / "work"
        work.mkdir()
        self.app.game.set(str(game))
        self.app.work.set(str(work))
        return game, work

    def test_version_stays_at_bottom_right_when_window_is_resized(self):
        from voice_randomizer import __version__

        self.assertEqual(self.root.title(), "Fallout 4 Voice Randomizer")

        def widgets(parent):
            for child in parent.winfo_children():
                yield child
                yield from widgets(child)

        labels = [
            widget for widget in widgets(self.root)
            if isinstance(widget, ttk.Label) and widget["text"] == f"v{__version__}"
        ]
        step_frames = {
            widget["text"]: widget
            for widget in widgets(self.root)
            if isinstance(widget, ttk.LabelFrame)
            and widget["text"] in (
                "1. Choose folders", "2. Choose mod settings",
                "3. Create and install",
            )
        }
        self.assertEqual(len(labels), 1)
        self.assertEqual(len(step_frames), 3)
        label = labels[0]
        self.root.deiconify()
        for size in ("760x680", "1000x900", "1200x1100"):
            with self.subTest(size=size):
                self.root.geometry(size)
                self.root.update()
                self.assertGreaterEqual(self.root.winfo_width(), 760)
                self.assertGreaterEqual(self.root.winfo_height(), 680)
                for step, widget in step_frames.items():
                    with self.subTest(step=step):
                        self.assertGreaterEqual(
                            widget.winfo_rootx(), self.root.winfo_rootx()
                        )
                        self.assertLessEqual(
                            widget.winfo_rootx() + widget.winfo_width(),
                            self.root.winfo_rootx() + self.root.winfo_width(),
                        )
                        self.assertGreaterEqual(
                            widget.winfo_rooty(), self.root.winfo_rooty()
                        )
                        self.assertLessEqual(
                            widget.winfo_rooty() + widget.winfo_height(),
                            self.root.winfo_rooty() + self.root.winfo_height(),
                        )
                right_gap = self.root.winfo_rootx() + self.root.winfo_width() - (
                    label.winfo_rootx() + label.winfo_width()
                )
                bottom_gap = self.root.winfo_rooty() + self.root.winfo_height() - (
                    label.winfo_rooty() + label.winfo_height()
                )
                self.assertEqual(right_gap, 16)
                self.assertEqual(bottom_gap, 16)

    def test_ui_is_grouped_into_ordered_workflow_steps(self):
        def widgets(parent):
            for child in parent.winfo_children():
                yield child
                yield from widgets(child)

        labels = {
            widget["text"] for widget in widgets(self.root)
            if isinstance(widget, ttk.LabelFrame)
        }
        self.assertTrue({
            "1. Choose folders", "2. Choose mod settings", "3. Create and install"
        }.issubset(labels))

    def test_folder_selections_are_separated(self):
        separator = self.app.folder_separator
        self.assertIsInstance(separator, tk.Frame)
        self.assertEqual(separator.master["text"], "1. Choose folders")
        self.assertEqual(str(separator["background"]), "System3dLight")
        self.assertEqual(int(separator.grid_info()["row"]), 3)

    def test_available_modes_can_be_selected(self):
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
        self.assertEqual(set(buttons), {"character", "player", "sounds", "chaos"})
        self.assertTrue(all("(not available)" not in button["text"] for button in buttons.values()))
        self.assertEqual(self.app.mode.get(), "player")
        self.assertFalse(buttons["player"].instate(["disabled"]))
        self.assertFalse(buttons["character"].instate(["disabled"]))
        self.assertFalse(buttons["sounds"].instate(["disabled"]))
        self.assertTrue(buttons["chaos"].instate(["disabled"]))

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
        self.assertEqual(buttons["ba2"]["text"], "BA2 archive files (not available)")
        buttons["ba2"].invoke()
        self.assertEqual(self.app.output.get(), "loose")

    def test_output_section_explains_file_format_and_zip_package(self):
        self.assertEqual(self.app.output_frame["text"], "Mod output")
        labels = [
            widget["text"] for widget in self.app.output_frame.winfo_children()
            if isinstance(widget, ttk.Label)
        ]
        self.assertIn("File format", labels)
        package_help = next(
            text for text in labels
            if "Fallout4VoiceRandomizer.zip" in text
        )
        self.assertIn("output folder", package_help)
        self.assertIn("mod folder", package_help)
        self.assertIn("manual packaging", package_help)

    def test_zip_checkbox_is_marked_recommended(self):
        def widgets(parent):
            for child in parent.winfo_children():
                yield child
                yield from widgets(child)

        checkboxes = [
            widget for widget in widgets(self.root)
            if isinstance(widget, ttk.Checkbutton)
            and str(widget["variable"]) == str(self.app.create_zip)
        ]
        self.assertEqual(len(checkboxes), 1)
        self.assertEqual(
            checkboxes[0]["text"], "Create ZIP for mod manager (recommended)"
        )

    def test_folder_guidance_shows_expected_output_and_blocks_missing_game(self):
        game, work = self.set_valid_folders()
        self.assertIn("Ready", self.app.game_feedback.get())
        self.assertIn("Ready", self.app.work_feedback.get())
        self.assertEqual(
            self.app.output_path_preview.get(),
            f"Expected output: {work / 'Fallout4VoiceRandomizer'}",
        )
        self.assertEqual(str(self.app.start["state"]), "normal")

        (work / "Fallout4VoiceRandomizer").mkdir()
        self.app._refresh_path_guidance()
        self.assertIn("Output already exists", self.app.work_feedback.get())
        self.assertEqual(str(self.app.start["state"]), "normal")

        self.app.work.set("")
        self.assertIn("Select a working folder", self.app.work_feedback.get())
        self.assertEqual(str(self.app.start["state"]), "disabled")
        self.app.work.set(str(work))

        self.app.game.set(str(game / "missing"))
        self.assertIn("does not exist", self.app.game_feedback.get())
        self.assertEqual(str(self.app.start["state"]), "disabled")

    def test_working_folder_must_be_outside_game_and_its_parent_folders(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        game = root / "Steam" / "common" / "Fallout 4"
        (game / "Data").mkdir(parents=True)
        (game / "Fallout4.exe").touch()
        self.app.game.set(str(game))

        for work in (game / "mods", game, game.parent):
            with self.subTest(work=work):
                self.app.work.set(str(work))
                self.assertIn("outside the game folder", self.app.work_feedback.get())
                self.assertEqual(str(self.app.start["state"]), "disabled")

    def test_new_seed_updates_seed_field(self):
        self.set_valid_folders()
        previous = self.app.seed.get()
        with patch("voice_randomizer.__main__.secrets.token_hex", return_value="new-seed"):
            self.app.generate_seed()
        self.assertEqual(self.app.seed.get(), "new-seed")
        self.assertNotEqual(self.app.seed.get(), previous)
        self.app.seed.set("  ")
        self.assertIn("Enter a seed", self.app.seed_feedback.get())
        self.assertEqual(str(self.app.start["state"]), "disabled")

    def test_completion_instructions_appear_in_main_window_for_built_output_type(self):
        self.set_valid_folders()
        for output_type, create_zip in (
            ("loose", False), ("loose", True), ("ba2", False), ("ba2", True)
        ):
            with self.subTest(output_type=output_type, create_zip=create_zip):
                self.app.output.set("ba2" if output_type == "loose" else "loose")
                self.app.create_zip.set(not create_zip)
                self.app.events.put(("done", r"C:\test\output", output_type, create_zip))
                with patch("voice_randomizer.__main__.messagebox.showinfo") as showinfo:
                    self.app.poll()
                showinfo.assert_not_called()
                self.assertEqual(self.app.status.get(), "Mod created.")
                self.assertEqual(
                    self.app.instructions_frame.master["text"],
                    "3. Create and install",
                )
                message = self.app.install_instructions.get("1.0", "end-1c")
                self.assertIn(r"C:\test\output", message)
                self.assertEqual(str(self.app.open_output["state"]), "normal")
                with patch.object(self.app, "open_folder") as open_folder:
                    self.app.open_output_folder()
                open_folder.assert_called_once_with(Path(r"C:\test\output"))
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
        self.set_valid_folders()
        self.assertIn("will appear here", self.app.install_instructions.get("1.0", "end-1c"))
        self.app._set_install_instructions("Old instructions")
        with patch("voice_randomizer.__main__.threading.Thread"):
            self.app.start_run()
        self.assertIn("will appear here", self.app.install_instructions.get("1.0", "end-1c"))
        self.assertEqual(self.app.status.get(), "Starting...")
        self.assertEqual(str(self.app.cancel["state"]), "normal")
        self.assertEqual(str(self.app.start["state"]), "disabled")

    def test_activity_moves_below_progress_bar_and_stops_on_completion(self):
        self.set_valid_folders()
        self.root.deiconify()
        bar = self.app.bar
        indicator = self.app.activity_indicator
        creation = bar.master
        for size in ("760x680", "1000x1000"):
            with self.subTest(size=size):
                self.root.geometry(size)
                self.root.update()
                self.assertGreaterEqual(
                    indicator.winfo_rooty(), bar.winfo_rooty() + bar.winfo_height()
                )
                self.assertEqual(
                    indicator.winfo_rootx() + indicator.winfo_width(),
                    bar.winfo_rootx() + bar.winfo_width(),
                )
                self.assertLessEqual(
                    indicator.winfo_rooty() + indicator.winfo_height(),
                    creation.winfo_rooty() + creation.winfo_height(),
                )
        self.assertEqual(self.app.activity_symbol.get(), "")

        with patch("voice_randomizer.__main__.threading.Thread"):
            self.app.start.invoke()
        initial = self.app.activity_symbol.get()
        self.assertTrue(initial)
        self.app.poll()
        self.assertNotEqual(self.app.activity_symbol.get(), initial)

        self.app.events.put(("done", r"C:\test\output", "loose", False))
        self.app.poll()
        self.assertEqual(self.app.activity_symbol.get(), "")
        self.app.poll()
        self.assertEqual(self.app.activity_symbol.get(), "")

    def test_activity_advances_during_scheduled_polling(self):
        self.set_valid_folders()
        scheduled = []

        def schedule(*args):
            callback_id = tk.Misc.after(self.root, *args)
            scheduled.append(callback_id)
            return callback_id

        self.root.after.side_effect = schedule
        with patch("voice_randomizer.__main__.threading.Thread"):
            self.app.start_run()
        initial = self.app.activity_symbol.get()
        observed = []
        self.root.after(100, self.app.poll)
        self.root.after(150, lambda: observed.append(self.app.activity_symbol.get()))
        self.root.after(200, lambda: self.app.events.put(("cancelled",)))
        self.root.after(350, self.root.quit)
        self.root.mainloop()
        for callback_id in scheduled:
            self.root.after_cancel(callback_id)
        self.assertTrue(observed)
        self.assertNotEqual(observed[0], initial)
        self.assertEqual(self.app.activity_symbol.get(), "")

    def test_activity_stops_on_cancellation_and_failure(self):
        self.set_valid_folders()
        for event in (("cancelled",), ("error", "test failure")):
            with self.subTest(event=event[0]):
                with patch("voice_randomizer.__main__.threading.Thread"):
                    self.app.start_run()
                self.assertTrue(self.app.activity_symbol.get())
                if event[0] == "cancelled":
                    self.app.cancel_run()
                    self.assertTrue(self.app.activity_symbol.get())
                self.app.events.put(event)
                with patch("voice_randomizer.__main__.messagebox.showerror"):
                    self.app.poll()
                self.assertEqual(self.app.activity_symbol.get(), "")

    def test_invalid_settings_do_not_start_activity(self):
        self.set_valid_folders()
        self.app.seed.set("")
        with patch("voice_randomizer.__main__.threading.Thread") as thread:
            self.app.start_run()
        thread.assert_not_called()
        self.assertEqual(self.app.activity_symbol.get(), "")

    def test_zip_selection_is_passed_to_build(self):
        self.set_valid_folders()
        self.assertTrue(self.app.create_zip.get())
        self.app.seed.set("repeatable-seed")
        with patch("voice_randomizer.__main__.threading.Thread") as thread, patch(
            "voice_randomizer.__main__.build", return_value=Path("output")
        ) as build:
            self.app.start_run()
            self.app.create_zip.set(False)
            thread.call_args.kwargs["target"]()
        self.assertTrue(build.call_args.kwargs["create_zip"])
        self.assertEqual(build.call_args.args[3], "repeatable-seed")
        self.assertEqual(self.app.events.get_nowait()[-1], True)

    def test_progress_events_show_stage_and_percentage(self):
        self.app.events.put(("progress", "Processing voices", 25, 100))
        self.app.poll()
        self.assertEqual(self.app.status.get(), "Processing voices: 25 / 100")
        self.assertEqual(float(self.app.bar["value"]), 25)

        self.app.events.put(("progress", "Reading archives", 0, 1))
        self.app.poll()
        self.assertEqual(self.app.status.get(), "Reading game voice archives...")
        self.assertEqual(str(self.app.bar["mode"]), "indeterminate")

        self.app.events.put(("progress", "Processing voices", 25, 100))
        self.app.poll()
        self.assertEqual(str(self.app.bar["mode"]), "determinate")

    def test_sound_mode_progress_and_exclusion_summary(self):
        self.app.mode.set("sounds")
        self.app.events.put(("progress", "Reading archives", 0, 1))
        self.app.poll()
        self.assertEqual(self.app.status.get(), "Reading game sound archives...")
        self.app.events.put(("done", r"C:\test\output", "loose", False,
                             "Excluded 3 sound paths. See the manifest and log for details."))
        self.app.poll()
        self.assertIn("Excluded 3 sound paths", self.app.status.get())

    def test_cancel_request_and_cancelled_result_have_clear_states(self):
        self.set_valid_folders()
        with patch("voice_randomizer.__main__.threading.Thread"):
            self.app.start_run()
        self.app.cancel_run()
        self.assertEqual(self.app.status.get(), "Cancelling... Please wait for the current step to stop.")
        self.assertTrue(self.app.cancel_event.is_set())
        self.assertEqual(str(self.app.cancel["state"]), "disabled")

        self.app.events.put(("cancelled",))
        self.app.poll()
        self.assertEqual(self.app.status.get(), "Cancelled. No mod was published.")
        self.assertEqual(str(self.app.cancel["state"]), "disabled")
        self.assertEqual(str(self.app.start["state"]), "normal")

    def test_failed_result_shows_error_dialog_and_status(self):
        self.set_valid_folders()
        self.app.running = True
        self.app.start.configure(state="disabled")
        self.app.events.put(("error", "ValueError: test failure"))
        with patch("voice_randomizer.__main__.messagebox.showerror") as showerror:
            self.app.poll()
        self.assertEqual(self.app.status.get(), "The run failed. No mod was published.")
        showerror.assert_called_once()

    def test_character_selection_is_passed_to_build(self):
        self.set_valid_folders()
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
