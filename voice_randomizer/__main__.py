"""Windows desktop interface for the voice randomizer."""

import os
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
        root.minsize(630, 490)
        self.game = tk.StringVar(value=str(_default_game_path()))
        self.work = tk.StringVar(value=str(_default_work_path()))
        self.output = tk.StringVar(value="loose")
        self.create_zip = tk.BooleanVar(value=True)
        self.mode = tk.StringVar(value="player")
        self.seed = tk.StringVar(value=secrets.token_hex(8))
        self.status = tk.StringVar(value="Select the game and working folders.")
        self.cancel_event = threading.Event()
        self.events: queue.Queue[tuple] = queue.Queue()
        self.running = False

        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(10, weight=1)
        self._folder(frame, 0, "Fallout 4 folder", self.game)
        self._folder(frame, 1, "Working folder", self.work)
        modes = ttk.LabelFrame(frame, text="Randomization mode", padding=8)
        modes.grid(row=2, column=0, columnspan=3, sticky="ew", pady=6)
        for label, value, state in (
            ("Within each character/NPC", "character", "normal"),
            ("Main character voices", "player", "normal"),
            ("Sounds only", "sounds", "disabled"),
            ("Chaos mode: sounds and voices", "chaos", "disabled"),
            ("Austin. mode", "austin", "disabled"),
        ):
            ttk.Radiobutton(modes, text=label, variable=self.mode, value=value,
                            state=state).pack(anchor="w")
        ttk.Label(frame, text="Output").grid(row=3, column=0, sticky="w", pady=6)
        choices = ttk.Frame(frame)
        choices.grid(row=3, column=1, sticky="w")
        output_types = ttk.Frame(choices)
        output_types.pack(anchor="w")
        ttk.Radiobutton(output_types, text="Loose files", variable=self.output, value="loose").pack(side="left")
        ttk.Radiobutton(output_types, text="BA2 archives", variable=self.output,
                        value="ba2", state="disabled").pack(side="left", padx=12)
        ttk.Checkbutton(choices, text="Create mod ZIP", variable=self.create_zip).pack(anchor="w")
        ttk.Label(frame, text="Seed").grid(row=4, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=self.seed).grid(row=4, column=1, sticky="ew")
        ttk.Label(frame, text="Creates a separate mod without changing your game files. "
                  "Shuffled voices may not match lip movements or subtitles. "
                  "BA2 output is experimental and has not been tested in game.",
                  wraplength=560).grid(row=5, column=0, columnspan=3, sticky="w", pady=8)
        self.bar = ttk.Progressbar(frame, maximum=100)
        self.bar.grid(row=6, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Label(frame, textvariable=self.status, wraplength=560).grid(
            row=7, column=0, columnspan=3, sticky="w")
        self.instructions_frame = ttk.Frame(frame)
        self.instructions_frame.grid(row=8, column=0, columnspan=3, sticky="ew")
        self.install_instructions = tk.Text(
            self.instructions_frame, height=10, width=65, wrap="word", state="disabled"
        )
        instructions_scrollbar = ttk.Scrollbar(
            self.instructions_frame, orient="vertical", command=self.install_instructions.yview
        )
        self.install_instructions.configure(yscrollcommand=instructions_scrollbar.set)
        self.install_instructions.pack(side="left", fill="both", expand=True)
        instructions_scrollbar.pack(side="right", fill="y")
        self._set_install_instructions("Installation instructions will appear here after the mod is created.")
        buttons = ttk.Frame(frame)
        buttons.grid(row=9, column=0, columnspan=3, sticky="w", pady=10)
        self.start = ttk.Button(buttons, text="Create mod", command=self.start_run)
        self.start.pack(side="left")
        self.cancel = ttk.Button(buttons, text="Cancel", command=self.cancel_run, state="disabled")
        self.cancel.pack(side="left", padx=6)
        ttk.Button(buttons, text="Open working folder", command=lambda: self.open_folder(self.work)).pack(side="left", padx=6)
        ttk.Button(buttons, text="Open game folder", command=lambda: self.open_folder(self.game)).pack(side="left")
        ttk.Label(frame, text=f"v{__version__}").grid(
            row=10, column=0, columnspan=3, sticky="se"
        )
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)

    def _folder(self, frame: ttk.Frame, row: int, label: str, value: tk.StringVar) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=value).grid(row=row, column=1, sticky="ew", padx=6)
        ttk.Button(frame, text="Browse", command=lambda: self.browse(value)).grid(row=row, column=2)

    def browse(self, value: tk.StringVar) -> None:
        folder = filedialog.askdirectory(parent=self.root)
        if folder:
            value.set(folder)

    def open_folder(self, value: tk.StringVar) -> None:
        try:
            if not value.get() or not Path(value.get()).is_dir():
                raise ValueError("Select an existing folder first.")
            if os.name != "nt":
                raise OSError("Opening folders is supported on Windows.")
            os.startfile(value.get())
        except (OSError, ValueError) as error:
            messagebox.showerror("Cannot open folder", str(error), parent=self.root)

    def start_run(self) -> None:
        if not self.game.get() or not self.work.get():
            messagebox.showerror("Missing folder", "Select both folders.", parent=self.root)
            return
        game, work, output, seed, mode = self.game.get(), self.work.get(), self.output.get(), self.seed.get(), self.mode.get()
        create_zip = self.create_zip.get()
        self._set_install_instructions("Installation instructions will appear here after the mod is created.")
        self.cancel_event.clear()
        self.running = True
        self.start.config(state="disabled")
        self.cancel.config(state="normal")
        self.bar["value"] = 0
        def run() -> None:
            try:
                result = build(Path(game), Path(work), output, seed,
                               lambda text, current, total: self.events.put(("progress", text, current, total)),
                               self.cancel_event.is_set, create_zip=create_zip, mode=mode)
                self.events.put(("done", str(result), output, create_zip))
            except Cancelled:
                self.events.put(("cancelled",))
            except Exception as error:
                self.events.put(("error", f"{type(error).__name__}: {error}"))
        threading.Thread(target=run, daemon=True).start()

    def cancel_run(self) -> None:
        self.cancel_event.set()
        self.status.set("Cancelling...")

    def _set_install_instructions(self, text: str) -> None:
        self.install_instructions.configure(state="normal")
        self.install_instructions.delete("1.0", "end")
        self.install_instructions.insert("1.0", text)
        self.install_instructions.configure(state="disabled")

    def close(self) -> None:
        if self.running:
            messagebox.showinfo("Run in progress", "Cancel the run and wait for it to stop.", parent=self.root)
        else:
            self.root.destroy()

    def poll(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "progress":
                    self.status.set(f"{event[1]}: {event[2]} / {event[3]}")
                    self.bar["value"] = 100 * event[2] / event[3]
                else:
                    self.running = False
                    self.start.config(state="normal")
                    self.cancel.config(state="disabled")
                    if event[0] == "done":
                        self.status.set("Mod created.")
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
                        messagebox.showinfo(
                            "Mod created",
                            "Completed successfully. Installation instructions are in the main window.",
                            parent=self.root,
                        )
                    elif event[0] == "cancelled":
                        self.status.set("Cancelled. No mod was published.")
                    else:
                        self.status.set("The run failed. No mod was published.")
                        messagebox.showerror("Run failed", event[1], parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


def main() -> None:
    root = tk.Tk()
    Application(root)
    root.mainloop()


if __name__ == "__main__":
    main()
