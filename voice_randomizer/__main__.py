"""Windows desktop interface for the voice randomizer."""

import os
import json
import queue
import secrets
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .pipeline import Cancelled, build


def _default_game_path() -> Path:
    program_files = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    return Path(program_files) / "Steam" / "steamapps" / "common" / "Fallout 4"


def _default_work_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


class Application:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Fallout 4 Voice Randomizer")
        root.minsize(760, 680)
        self.game = tk.StringVar(value=str(_default_game_path()))
        self.work = tk.StringVar(value=str(_default_work_path()))
        self.output = tk.StringVar(value="loose")
        self.create_zip = tk.BooleanVar(value=True)
        self.mode = tk.StringVar(value="player")
        self.seed = tk.StringVar(value=secrets.token_hex(8))
        self.status = tk.StringVar(value="Choose folders and settings to create a mod.")
        self.activity_symbol = tk.StringVar(value="")
        self._activity_step = 0
        self.game_feedback = tk.StringVar()
        self.work_feedback = tk.StringVar()
        self.output_path_preview = tk.StringVar()
        self.seed_feedback = tk.StringVar()
        self.cancel_event = threading.Event()
        self.events: queue.Queue[tuple] = queue.Queue()
        self.running = False
        self.path_error: str | None = None
        self.last_output_path: Path | None = None
        self._progress_indeterminate = False
        self.input_widgets: list[tk.Widget] = []

        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)

        folders = ttk.LabelFrame(frame, text="1. Choose folders", padding=8)
        folders.grid(row=0, column=0, sticky="ew")
        folders.columnconfigure(1, weight=1)
        ttk.Label(
            folders, text="Select the game installation and a separate working folder."
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        self._folder(folders, 1, "Fallout 4 folder", self.game, self.game_feedback)
        self.folder_separator = tk.Frame(
            folders, height=1, background="System3dLight",
            bd=0, highlightthickness=0,
        )
        self.folder_separator.grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=6
        )
        self._folder(folders, 4, "Working folder", self.work, self.work_feedback)
        ttk.Label(folders, textvariable=self.output_path_preview, wraplength=700).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(4, 0)
        )

        settings = ttk.LabelFrame(frame, text="2. Choose mod settings", padding=8)
        settings.grid(row=1, column=0, sticky="ew", pady=6)
        settings.columnconfigure(1, weight=1)
        modes = ttk.LabelFrame(settings, text="Randomization mode", padding=6)
        modes.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        for label, value, state in (
            ("Within each character/NPC", "character", "normal"),
            ("Main character voices", "player", "normal"),
            ("Sounds only", "sounds", "normal"),
            ("Chaos mode: sounds and voices", "chaos", "disabled"),
        ):
            button = ttk.Radiobutton(
                modes, text=label, variable=self.mode, value=value, state=state
            )
            button.pack(anchor="w")
            if state == "normal":
                self.input_widgets.append(button)

        self.output_frame = ttk.LabelFrame(settings, text="Mod output", padding=6)
        self.output_frame.grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(0, 4)
        )
        self.output_frame.columnconfigure(1, weight=1)
        ttk.Label(self.output_frame, text="File format").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        output_types = ttk.Frame(self.output_frame)
        output_types.grid(row=0, column=1, sticky="w")
        loose = ttk.Radiobutton(
            output_types, text="Loose files", variable=self.output, value="loose"
        )
        loose.pack(side="left")
        self.input_widgets.append(loose)
        ttk.Radiobutton(
            output_types, text="BA2 archive files (not available)", variable=self.output,
            value="ba2", state="disabled"
        ).pack(side="left", padx=12)
        self.zip_checkbox = ttk.Checkbutton(
            self.output_frame, text="Create ZIP for mod manager (recommended)",
            variable=self.create_zip
        )
        self.zip_checkbox.grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        self.input_widgets.append(self.zip_checkbox)
        ttk.Label(
            self.output_frame,
            text="Creates Fallout4VoiceRandomizer.zip in the output folder. "
            "Clear this option to leave the files in the mod folder for manual packaging.",
            wraplength=700,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))

        ttk.Label(settings, text="Seed").grid(row=2, column=0, sticky="w", pady=3)
        seed_controls = ttk.Frame(settings)
        seed_controls.grid(row=2, column=1, sticky="ew")
        seed_controls.columnconfigure(0, weight=1)
        self.seed_entry = ttk.Entry(seed_controls, textvariable=self.seed)
        self.seed_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.input_widgets.append(self.seed_entry)
        self.new_seed_button = ttk.Button(
            seed_controls, text="New seed", command=self.generate_seed
        )
        self.new_seed_button.grid(row=0, column=1)
        self.input_widgets.append(self.new_seed_button)
        ttk.Label(settings, textvariable=self.seed_feedback, wraplength=700).grid(
            row=3, column=1, sticky="w", pady=(2, 0)
        )
        ttk.Label(
            settings,
            text="The app leaves your game files unchanged. Shuffled voices will not "
            "match lip movements or subtitles. Sounds only excludes voice files and "
            "music-related paths, but some effects can contain speech or music.",
            wraplength=700,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        creation = ttk.LabelFrame(frame, text="3. Create and install", padding=8)
        creation.grid(row=2, column=0, sticky="nsew")
        creation.columnconfigure(0, weight=1)
        creation.rowconfigure(4, weight=1)
        buttons = ttk.Frame(creation)
        buttons.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.start = ttk.Button(buttons, text="Create mod", command=self.start_run)
        self.start.pack(side="left")
        self.cancel = ttk.Button(
            buttons, text="Cancel", command=self.cancel_run, state="disabled"
        )
        self.cancel.pack(side="left", padx=6)
        self.open_output = ttk.Button(
            buttons, text="Open output folder", command=self.open_output_folder,
            state="disabled"
        )
        self.open_output.pack(side="left", padx=6)
        ttk.Button(
            buttons, text="Open working folder",
            command=lambda: self.open_folder(self.work)
        ).pack(side="left", padx=6)
        ttk.Button(
            buttons, text="Open game folder",
            command=lambda: self.open_folder(self.game)
        ).pack(side="left")
        self.bar = ttk.Progressbar(creation, maximum=100)
        self.bar.grid(row=1, column=0, sticky="ew", pady=4)
        status_row = ttk.Frame(creation)
        status_row.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        status_row.columnconfigure(0, weight=1)
        self.activity_indicator = ttk.Label(
            status_row, textvariable=self.activity_symbol, width=2, anchor="e"
        )
        self.activity_indicator.grid(row=0, column=1, sticky="e")
        ttk.Label(status_row, textvariable=self.status, wraplength=680).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(creation, text="Installation instructions").grid(
            row=3, column=0, sticky="w"
        )
        self.instructions_frame = ttk.Frame(creation)
        self.instructions_frame.grid(row=4, column=0, sticky="nsew")
        self.install_instructions = tk.Text(
            self.instructions_frame, height=6, width=65, wrap="word", state="disabled"
        )
        instructions_scrollbar = ttk.Scrollbar(
            self.instructions_frame, orient="vertical", command=self.install_instructions.yview
        )
        self.install_instructions.configure(yscrollcommand=instructions_scrollbar.set)
        self.install_instructions.pack(side="left", fill="both", expand=True)
        instructions_scrollbar.pack(side="right", fill="y")
        self._set_install_instructions(
            "The output path and installation steps will appear here after the mod is created."
        )
        ttk.Label(frame, text=f"v{__version__}").grid(
            row=3, column=0, sticky="se", pady=(6, 0)
        )
        self.game.trace_add("write", self._refresh_path_guidance)
        self.work.trace_add("write", self._refresh_path_guidance)
        self.seed.trace_add("write", self._update_create_state)
        self._refresh_path_guidance()
        self._update_create_state()
        root.update_idletasks()
        root.minsize(760, max(680, root.winfo_reqheight()))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)

    def _folder(
        self, frame: ttk.LabelFrame, row: int, label: str, value: tk.StringVar,
        feedback: tk.StringVar,
    ) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=3)
        entry = ttk.Entry(frame, textvariable=value)
        entry.grid(row=row, column=1, sticky="ew", padx=6)
        browse = ttk.Button(frame, text="Browse", command=lambda: self.browse(value))
        browse.grid(row=row, column=2)
        ttk.Label(frame, textvariable=feedback, wraplength=700).grid(
            row=row + 1, column=0, columnspan=3, sticky="w", pady=(0, 3)
        )
        self.input_widgets.extend((entry, browse))

    def _refresh_path_guidance(self, *_: str) -> None:
        game_path = None
        work_path = None
        game_error = None
        work_error = None
        game_text = self.game.get().strip()
        work_text = self.work.get().strip()

        if not game_text:
            game_error = "Select the Fallout 4 folder."
            self.game_feedback.set("Choose the folder that contains Fallout4.exe and Data.")
        else:
            try:
                game_path = Path(game_text).expanduser().resolve()
            except (OSError, RuntimeError, ValueError) as error:
                game_error = f"Cannot use the Fallout 4 folder: {error}"
                self.game_feedback.set(game_error)
            else:
                if not game_path.is_dir():
                    game_error = "The Fallout 4 folder does not exist."
                    self.game_feedback.set(game_error)
                elif not (game_path / "Fallout4.exe").is_file() or not (
                    game_path / "Data"
                ).is_dir():
                    game_error = "The folder must contain Fallout4.exe and a Data folder."
                    self.game_feedback.set(game_error)
                else:
                    self.game_feedback.set("Ready. Fallout4.exe and Data were found.")

        if not work_text:
            work_error = "Select a working folder."
            self.work_feedback.set(
                "Select a working folder outside the game installation."
            )
        else:
            try:
                work_path = Path(work_text).expanduser().resolve()
            except (OSError, RuntimeError, ValueError) as error:
                work_error = f"Cannot use the working folder: {error}"
                self.work_feedback.set(work_error)
            else:
                existing = work_path
                while not existing.exists() and existing.parent != existing:
                    existing = existing.parent
                if not existing.is_dir():
                    work_error = (
                        "The working folder cannot be created because an existing path "
                        "component is not a folder."
                    )
                    self.work_feedback.set(work_error)
                else:
                    self.work_feedback.set("Ready. The app can create this folder if needed.")

        if game_path is not None and work_path is not None and (
            game_path == work_path
            or game_path in work_path.parents
            or work_path in game_path.parents
        ):
            work_error = (
                "Choose a working folder outside the game folder, its parent folders, "
                "and its subfolders."
            )
            self.work_feedback.set(work_error)

        if work_path is None:
            self.output_path_preview.set("Expected output: select a working folder.")
        else:
            output_path = work_path / "Fallout4VoiceRandomizer"
            self.output_path_preview.set(f"Expected output: {output_path}")
            if work_error is None and output_path.exists():
                self.work_feedback.set(
                    f"Output already exists: {output_path}. Move or rename it before "
                    "creating another mod."
                )

        self.path_error = game_error or work_error
        self._update_create_state()

    def _update_create_state(self, *_: str) -> None:
        if not hasattr(self, "start"):
            return
        self.seed_feedback.set(
            "Enter a seed to continue."
            if not self.seed.get().strip()
            else "Use the same seed, game files, and app version to repeat a shuffle."
        )
        can_start = not self.running and not self.path_error and bool(self.seed.get().strip())
        self.start.configure(state="normal" if can_start else "disabled")

    def browse(self, value: tk.StringVar) -> None:
        folder = filedialog.askdirectory(parent=self.root)
        if folder:
            value.set(folder)

    def open_folder(self, value: tk.StringVar | Path | str) -> None:
        try:
            folder = value.get() if isinstance(value, tk.Variable) else str(value)
            if not folder or not Path(folder).is_dir():
                raise ValueError("Select an existing folder first.")
            if os.name != "nt":
                raise OSError("Opening folders is supported on Windows.")
            os.startfile(folder)
        except (OSError, ValueError) as error:
            messagebox.showerror("Cannot open folder", str(error), parent=self.root)

    def open_output_folder(self) -> None:
        if self.last_output_path is None:
            messagebox.showerror(
                "Output not available", "Create a mod first.", parent=self.root
            )
            return
        self.open_folder(self.last_output_path)

    def generate_seed(self) -> None:
        self.seed.set(secrets.token_hex(8))

    def start_run(self) -> None:
        self._refresh_path_guidance()
        if self.path_error:
            self.status.set(self.path_error)
            return
        if not self.seed.get().strip():
            self.status.set("Enter a seed before you create the mod.")
            return
        game = self.game.get().strip()
        work = self.work.get().strip()
        output = self.output.get()
        seed = self.seed.get().strip()
        mode = self.mode.get()
        create_zip = self.create_zip.get()
        self._set_install_instructions(
            "The output path and installation steps will appear here after the mod is created."
        )
        self.last_output_path = None
        self.open_output.configure(state="disabled")
        self.cancel_event.clear()
        self.running = True
        self._advance_activity()
        for widget in self.input_widgets:
            widget.configure(state="disabled")
        self.start.configure(state="disabled")
        self.cancel.configure(state="normal")
        self.bar["value"] = 0

        self.status.set("Starting...")

        def run() -> None:
            try:
                result = build(
                    Path(game), Path(work), output, seed,
                    lambda text, current, total: self.events.put(
                        ("progress", text, current, total)
                    ),
                    self.cancel_event.is_set, create_zip=create_zip, mode=mode
                )
                summary = ""
                if mode == "sounds":
                    report = json.loads((result / "manifest.json").read_text(encoding="utf-8"))
                    summary = (
                        f"Excluded {len(report['exclusions'])} sound paths. "
                        "See the manifest and log for details."
                    )
                if mode == "sounds":
                    self.events.put(("done", str(result), output, create_zip, summary))
                else:
                    self.events.put(("done", str(result), output, create_zip))
            except Cancelled:
                self.events.put(("cancelled",))
            except Exception as error:
                self.events.put(("error", f"{type(error).__name__}: {error}"))

        threading.Thread(target=run, daemon=True).start()

    def cancel_run(self) -> None:
        if self.running and not self.cancel_event.is_set():
            self.cancel_event.set()
            self.cancel.configure(state="disabled")
            self.status.set("Cancelling... Please wait for the current step to stop.")

    def _set_install_instructions(self, text: str) -> None:
        self.install_instructions.configure(state="normal")
        self.install_instructions.delete("1.0", "end")
        self.install_instructions.insert("1.0", text)
        self.install_instructions.configure(state="disabled")

    def close(self) -> None:
        if self.running:
            messagebox.showinfo(
                "Run in progress", "Cancel the run and wait for it to stop.",
                parent=self.root
            )
        else:
            self.root.destroy()

    def _stop_indeterminate_progress(self) -> None:
        if self._progress_indeterminate:
            self.bar.stop()
            self.bar.configure(mode="determinate")
            self._progress_indeterminate = False

    def _advance_activity(self) -> None:
        if self.running:
            frames = ("|", "/", "-", "\\")
            self.activity_symbol.set(frames[self._activity_step])
            self._activity_step = (self._activity_step + 1) % len(frames)

    def _update_progress(self, stage: str, current: int, total: int) -> None:
        if stage == "Reading archives":
            self.status.set(
                "Reading game sound archives..." if self.mode.get() == "sounds"
                else "Reading game voice archives..."
            )
            if not self._progress_indeterminate:
                self.bar.configure(mode="indeterminate")
                self.bar.start(12)
                self._progress_indeterminate = True
            return

        self._stop_indeterminate_progress()
        total = max(total, 1)
        current = min(max(current, 0), total)
        self.bar["value"] = current * 100 / total
        if stage in ("Creating ZIP", "Verifying ZIP", "Publishing output"):
            current_mb = current / (1024 * 1024)
            total_mb = total / (1024 * 1024)
            self.status.set(f"{stage}: {current_mb:.1f} / {total_mb:.1f} MB")
        else:
            self.status.set(f"{stage}: {current} / {total}")

    def poll(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "progress":
                    self._update_progress(event[1], event[2], event[3])
                else:
                    self.running = False
                    self.activity_symbol.set("")
                    self._stop_indeterminate_progress()
                    for widget in self.input_widgets:
                        widget.configure(state="normal")
                    self.cancel.configure(state="disabled")
                    if event[0] == "done":
                        summary = event[4] if len(event) > 4 else ""
                        self.status.set(f"Mod created. {summary}".strip())
                        self.bar["value"] = 100
                        self.last_output_path = Path(event[1])
                        self.open_output.configure(state="normal")
                        if event[3]:
                            package_step = (
                                "1. Open the output folder below and find Fallout4VoiceRandomizer.zip.\n"
                            )
                        elif event[2] == "loose":
                            package_step = (
                                "1. Open the output folder below. Zip the contents of its "
                                "mod folder, so sound is at the archive root.\n"
                            )
                        else:
                            package_step = (
                                "1. Open the output folder below. Zip the contents of its "
                                "mod folder, so the BA2 and ESL files are at the archive root.\n"
                            )
                        if event[2] == "loose":
                            instructions = (
                                f"To install loose files:\n{package_step}"
                                "2. Install the ZIP with your Fallout 4 mod manager and enable it.\n"
                                "3. Enable archive invalidation in your mod manager, or add "
                                "these lines under [Archive] in Documents\\My Games\\Fallout4"
                                "\\Fallout4Custom.ini (or your mod manager's profile INI):\n"
                                "bInvalidateOlderFiles=1\nsResourceDataDirsFinal="
                            )
                        else:
                            instructions = (
                                f"To install BA2 files:\n{package_step}"
                                "2. Install the ZIP with your Fallout 4 mod manager and enable it.\n"
                                "3. Enable all generated ESL plugins in your mod manager.\n\n"
                                "BA2 output is experimental and has not been tested in game."
                            )
                        self._set_install_instructions(f"{instructions}\n\nOutput: {event[1]}")
                    elif event[0] == "cancelled":
                        self.status.set("Cancelled. No mod was published.")
                    else:
                        self.status.set("The run failed. No mod was published.")
                        messagebox.showerror("Run failed", event[1], parent=self.root)
                    self._refresh_path_guidance()
        except queue.Empty:
            pass
        self._advance_activity()
        self.root.after(100, self.poll)


def main() -> None:
    root = tk.Tk()
    Application(root)
    root.mainloop()


if __name__ == "__main__":
    main()
