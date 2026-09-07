import queue
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk

import storage
from assistant import get_assistant


class OfficeAssistantGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Office AI Assistant")
        self.root.geometry("900x600")

        self.reply_queue: queue.Queue = queue.Queue()
        self.bot = None
        self.init_error = None

        self._build_layout()
        self._append_chat("System", "Starting assistant...")
        threading.Thread(target=self._init_bot, daemon=True).start()
        self.root.after(150, self._poll_queue)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        main = ttk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        chat_frame = ttk.Frame(main)
        chat_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.chat_display = scrolledtext.ScrolledText(
            chat_frame, wrap=tk.WORD, state="disabled", font=("Segoe UI", 10)
        )
        self.chat_display.pack(fill=tk.BOTH, expand=True)

        input_frame = ttk.Frame(chat_frame)
        input_frame.pack(fill=tk.X, pady=(8, 0))

        self.input_entry = ttk.Entry(input_frame, font=("Segoe UI", 10))
        self.input_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.input_entry.bind("<Return>", lambda _e: self._on_send())

        self.send_button = ttk.Button(input_frame, text="Send", command=self._on_send)
        self.send_button.pack(side=tk.LEFT, padx=(6, 0))

        self.status_label = ttk.Label(chat_frame, text="")
        self.status_label.pack(fill=tk.X, pady=(4, 0))

        side_frame = ttk.Frame(main, width=280)
        side_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(8, 0))
        side_frame.pack_propagate(False)

        ttk.Label(side_frame, text="Recent file operations", font=("Segoe UI", 10, "bold")).pack(
            anchor="w"
        )
        self.ops_list = tk.Listbox(side_frame, font=("Segoe UI", 9))
        self.ops_list.pack(fill=tk.BOTH, expand=True, pady=(4, 8))

        ttk.Button(side_frame, text="Refresh", command=self._refresh_operations).pack(fill=tk.X)

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------
    def _init_bot(self) -> None:
        try:
            self.bot = get_assistant()
            self.reply_queue.put(("system", "Ready. Ask me to create, read, edit, or open a Word/Excel file."))
        except Exception as exc:  # missing API key, etc.
            self.init_error = str(exc)
            self.reply_queue.put(("error", f"Failed to start: {exc}"))

    # ------------------------------------------------------------------
    # Chat helpers
    # ------------------------------------------------------------------
    def _append_chat(self, who: str, text: str) -> None:
        self.chat_display.configure(state="normal")
        self.chat_display.insert(tk.END, f"{who}: {text}\n\n")
        self.chat_display.configure(state="disabled")
        self.chat_display.see(tk.END)

    def _on_send(self) -> None:
        text = self.input_entry.get().strip()
        if not text or self.bot is None:
            return
        self.input_entry.delete(0, tk.END)
        self._append_chat("You", text)
        self.send_button.configure(state="disabled")
        self.status_label.configure(text="Thinking...")
        threading.Thread(target=self._run_send, args=(text,), daemon=True).start()

    def _run_send(self, text: str) -> None:
        try:
            reply = self.bot.send(text)
            self.reply_queue.put(("assistant", reply))
        except Exception as exc:
            self.reply_queue.put(("error", str(exc)))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, text = self.reply_queue.get_nowait()
                if kind == "assistant":
                    self._append_chat("Assistant", text)
                    self._refresh_operations()
                elif kind == "error":
                    self._append_chat("Error", text)
                elif kind == "system":
                    self._append_chat("System", text)
                self.status_label.configure(text="")
                self.send_button.configure(state="normal")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)

    def _refresh_operations(self) -> None:
        self.ops_list.delete(0, tk.END)
        for tool_name, file_path, detail, created_at in storage.recent_operations():
            label = f"{created_at[11:19]}  {tool_name}  {file_path}"
            self.ops_list.insert(tk.END, label)


def main() -> None:
    root = tk.Tk()
    OfficeAssistantGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
