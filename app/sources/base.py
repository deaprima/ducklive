"""Dasar sumber event: thread, auto-reconnect, dan status untuk Admin."""

import threading


class FatalSourceError(Exception):
    """Error yang tidak akan hilang dengan mencoba lagi (URL kosong, belum login, kuota habis, live selesai)."""


class Source:
    """Antarmuka sumber event. Implementasi memanggil emit(...) untuk tiap event.

    emit(ext_id, kind, author, units=1, amount=None, currency=None)
      kind: "member" | "gift" | "superchat"

    Error biasa (jaringan putus, server YouTube error) -> otomatis disambung ulang dengan jeda
    yang makin panjang. FatalSourceError atau run() yang selesai normal -> berhenti.
    """

    name = "base"
    RETRY_DELAYS = (5, 10, 20, 40, 60)

    def __init__(self, emit, settings_getter, notify=None):
        self.emit = emit
        self.get_settings = settings_getter
        self.notify = notify or (lambda text: None)
        self._stop = threading.Event()
        self._thread = None
        self.error = ""
        self.info = ""
        self.retries = 0

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self.error = ""
        self.retries = 0
        self._thread = threading.Thread(target=self._safe_run, daemon=True, name=f"source-{self.name}")
        self._thread.start()

    def stop(self):
        self._stop.set()

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def sleep(self, seconds) -> bool:
        """Tunggu, tapi bangun segera saat stop(). True jika diminta berhenti."""
        return self._stop.wait(seconds)

    def connected(self):
        """Dipanggil implementasi setelah berhasil tersambung; mereset hitungan retry."""
        if self.retries:
            self.notify(f"Sumber {self.name} tersambung kembali")
        self.retries = 0
        self.error = ""

    def _safe_run(self):
        while not self._stop.is_set():
            try:
                self.run()
                return
            except FatalSourceError as e:
                self.error = str(e)
                self.info = "berhenti"
                self.notify(f"Sumber {self.name} berhenti: {e}")
                return
            except Exception as e:  # tampilkan ke Admin dan coba lagi, jangan matikan server
                if self._stop.is_set():
                    return
                delay = self.RETRY_DELAYS[min(self.retries, len(self.RETRY_DELAYS) - 1)]
                self.retries += 1
                self.error = f"{type(e).__name__}: {e}"
                self.info = f"terputus, mencoba lagi dalam {delay} dtk (percobaan ke-{self.retries})"
                if self.retries == 1:
                    self.notify(f"Sumber {self.name} terputus, menyambung ulang otomatis…")
                if self.sleep(delay):
                    return

    def run(self):
        raise NotImplementedError

    def status(self):
        return {
            "name": self.name,
            "running": self.running,
            "error": self.error,
            "info": self.info,
            "retries": self.retries,
        }
