"""
iPhone -> PC : приёмник AirPlay ("Повтор экрана") с интерфейсом в стиле Windows 11 (Fluent / WinUI).

Как пользоваться:
  1. python airplay_mirror.py
  2. На iPhone: Пункт управления -> «Повтор экрана» -> выбрать свой ПК
  3. Экран телефона появится на ПК

Сам приём зашифрованного потока AirPlay (FairPlay) делает UxPlay (uxplay.exe) —
этот скрипт запускает его автоматически, показывает статус и красивый интерфейс.

Требования (один раз):
  * Apple Bonjour (ставится вместе с iTunes / «Bonjour Print Services for Windows»)
  * uxplay.exe — положите рядом со скриптом (или в папку uxplay\\), либо в PATH
    https://github.com/FDH2/UxPlay  (там же инструкция по сборке под Windows)
  * iPhone и ПК — в одной Wi-Fi сети
Python-библиотеки скрипт доустановит сам.
"""
import ctypes
from ctypes import wintypes
import importlib
import os
import shutil
import subprocess
import sys
import winreg

# ---------------------------------------------------------------- зависимости
FROZEN = getattr(sys, "frozen", False)


def _ensure(pkg_pip, module):
    if FROZEN:  # в собранном .exe всё уже внутри
        return
    try:
        importlib.import_module(module)
    except ImportError:
        print(f"Устанавливаю {pkg_pip} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg_pip])

_ensure("PyQt6", "PyQt6")
_ensure("PyQt6-Fluent-Widgets", "qfluentwidgets")
_ensure("PyQt6-Frameless-Window", "qframelesswindow")

from PyQt6.QtCore import Qt, QProcess, QProcessEnvironment, QSettings, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QFont, QIcon
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QMenu, QStyle, QSystemTrayIcon, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, ComboBox, FluentIcon, IconWidget, InfoBar,
    InfoBarPosition, LineEdit, PlainTextEdit, PrimaryPushButton, PushButton,
    StrongBodyLabel, SwitchButton, Theme, TitleLabel, isDarkTheme, setTheme,
)
from qframelesswindow import FramelessWindow, StandardTitleBar

APP_DIR = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
APP_NAME = "iPhoneMirror"
INSTANCE_KEY = "iPhoneMirrorSingleInstance"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UXPLAY_REPO = "https://github.com/FDH2/UxPlay"


# ------------------------------------------------------------------- хелперы
def resource_path(name):
    for base in (getattr(sys, "_MEIPASS", None), APP_DIR):
        if base and os.path.isfile(os.path.join(base, name)):
            return os.path.join(base, name)
    return None


def autostart_command():
    if FROZEN:
        return f'"{sys.executable}" --tray'
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{pyw}" "{os.path.abspath(__file__)}" --tray'


def is_autostart():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass


MSYS_BINS = [r"C:\msys64\ucrt64\bin", r"C:\msys64\mingw64\bin"]


def find_uxplay():
    # Сначала uxplay.exe рядом со скриптом, потом сборка MSYS2, потом PATH
    candidates = [
        os.path.join(APP_DIR, "uxplay.exe"),
        os.path.join(APP_DIR, "uxplay", "uxplay.exe"),
    ] + [os.path.join(b, "uxplay.exe") for b in MSYS_BINS]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return shutil.which("uxplay")


def msys_bin_dir():
    for b in MSYS_BINS:
        if os.path.isdir(b):
            return b
    return None


def bonjour_installed():
    try:
        out = subprocess.run(
            ["sc", "query", "Bonjour Service"],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
        return "RUNNING" in out or "STOPPED" in out
    except Exception:
        return False


def run_as_admin(cmd, params):
    ctypes.windll.shell32.ShellExecuteW(None, "runas", cmd, params, None, 0)



# ----------------------------------------------- окно трансляции: не давать "замирать"
_user32 = ctypes.windll.user32
_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def restore_minimized_windows_of(pid):
    """Если окно процесса свёрнуто - вернуть его без активации и убрать на задний план.
    Свёрнутое окно Windows не перерисовывает, из-за чего картинка замирает,
    а Discord/OBS перестают его захватывать."""
    def cb(hwnd, _):
        wpid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid and _user32.IsWindowVisible(hwnd) and _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
            # HWND_BOTTOM, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
            _user32.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        return True
    _user32.EnumWindows(_EnumProc(cb), 0)

# --------------------------------------------------------------------- окно
class MirrorWindow(FramelessWindow):
    def __init__(self):
        super().__init__()
        self.proc = None
        self.uxplay = find_uxplay()
        self._quitting = False
        self._retries = 0
        self._tray_hint_shown = False
        self.settings = QSettings(APP_NAME, APP_NAME)
        icon_path = resource_path("icon.ico")
        self.app_icon = QIcon(icon_path) if icon_path else \
            self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.setWindowIcon(self.app_icon)

        self.setTitleBar(StandardTitleBar(self))
        self.titleBar.setTitle("Повтор экрана iPhone")
        self.resize(560, 820)
        self.setMinimumSize(480, 560)
        self.setStyleSheet("background: transparent;")
        try:  # Mica, как в Windows 11
            self.windowEffect.setMicaEffect(self.winId(), isDarkMode=isDarkTheme())
        except Exception:
            self.setStyleSheet(
                "background: #202020;" if isDarkTheme() else "background: #f3f3f3;"
            )

        self._build_ui()
        self._keep_timer = QTimer(self)
        self._keep_timer.timeout.connect(self._keep_tick)
        self._keep_timer.start(300)
        self._setup_tray()
        # первый запуск .exe: включаем автозапуск с Windows (можно выключить переключателем)
        if FROZEN and not self.settings.value("autostart_init", False, type=bool):
            try:
                set_autostart(True)
            except OSError:
                pass
            self.settings.setValue("autostart_init", True)
        self._sync_autostart_ui()
        # при автозапуске даём сети время подняться
        QTimer.singleShot(8000 if "--tray" in sys.argv else 400, self._auto_start)

    # ------------------------------------------------------------ интерфейс
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 48, 28, 24)
        root.setSpacing(14)

        root.addWidget(TitleLabel("Экран iPhone на ПК"))
        sub = BodyLabel("Откройте «Повтор экрана» на iPhone и выберите этот компьютер.")
        sub.setWordWrap(True)
        root.addWidget(sub)

        # --- карточка статуса
        self.status_card = CardWidget(self)
        sl = QHBoxLayout(self.status_card)
        sl.setContentsMargins(20, 18, 20, 18)
        sl.setSpacing(16)
        self.status_icon = IconWidget(FluentIcon.PHONE, self.status_card)
        self.status_icon.setFixedSize(40, 40)
        sl.addWidget(self.status_icon)
        tl = QVBoxLayout()
        tl.setSpacing(2)
        self.status_title = StrongBodyLabel("Остановлено")
        self.status_text = CaptionLabel("Нажмите «Запустить»")
        tl.addWidget(self.status_title)
        tl.addWidget(self.status_text)
        sl.addLayout(tl, 1)
        root.addWidget(self.status_card)

        # --- карточка настроек
        card = CardWidget(self)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(20, 16, 20, 16)
        cl.setSpacing(12)

        row = QHBoxLayout()
        row.addWidget(BodyLabel("Имя на iPhone"))
        self.name_edit = LineEdit()
        self.name_edit.setText(os.environ.get("COMPUTERNAME", "Windows PC"))
        self.name_edit.setFixedWidth(240)
        row.addStretch(1)
        row.addWidget(self.name_edit)
        cl.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(BodyLabel("Во весь экран"))
        row2.addStretch(1)
        self.fs_switch = SwitchButton()
        self.fs_switch.setOnText("Вкл")
        self.fs_switch.setOffText("Выкл")
        row2.addWidget(self.fs_switch)
        cl.addLayout(row2)

        row_ll = QHBoxLayout()
        row_ll.addWidget(BodyLabel("Низкая задержка"))
        row_ll.addStretch(1)
        self.ll_switch = SwitchButton()
        self.ll_switch.setChecked(True)
        self.ll_switch.setOnText("Вкл")
        self.ll_switch.setOffText("Выкл")
        row_ll.addWidget(self.ll_switch)
        cl.addLayout(row_ll)

        row_fps = QHBoxLayout()
        row_fps.addWidget(BodyLabel("60 кадров/с"))
        row_fps.addStretch(1)
        self.fps_switch = SwitchButton()
        self.fps_switch.setChecked(True)
        self.fps_switch.setOnText("Вкл")
        self.fps_switch.setOffText("Выкл")
        row_fps.addWidget(self.fps_switch)
        cl.addLayout(row_fps)

        row_keep = QHBoxLayout()
        row_keep.addWidget(BodyLabel("Сворачивание - на задний план"))
        row_keep.addStretch(1)
        self.keep_switch = SwitchButton()
        self.keep_switch.setChecked(True)
        self.keep_switch.setOnText("Вкл")
        self.keep_switch.setOffText("Выкл")
        row_keep.addWidget(self.keep_switch)
        cl.addLayout(row_keep)

        row_q = QHBoxLayout()
        row_q.addWidget(BodyLabel("Качество"))
        row_q.addStretch(1)
        self.quality = ComboBox()
        self.quality.addItems(["1080p", "1440p", "4K (HEVC, новые iPhone)"])
        self.quality.setCurrentIndex(1)
        self.quality.setFixedWidth(240)
        row_q.addWidget(self.quality)
        cl.addLayout(row_q)

        row_as = QHBoxLayout()
        row_as.addWidget(BodyLabel("Запускать вместе с Windows"))
        row_as.addStretch(1)
        self.autostart_switch = SwitchButton()
        self.autostart_switch.setOnText("Вкл")
        self.autostart_switch.setOffText("Выкл")
        self.autostart_switch.checkedChanged.connect(self._on_autostart_switch)
        row_as.addWidget(self.autostart_switch)
        cl.addLayout(row_as)

        row3 = QHBoxLayout()
        row3.addWidget(BodyLabel("Запускать приём автоматически"))
        row3.addStretch(1)
        self.auto_switch = SwitchButton()
        self.auto_switch.setChecked(True)
        self.auto_switch.setOnText("Вкл")
        self.auto_switch.setOffText("Выкл")
        row3.addWidget(self.auto_switch)
        cl.addLayout(row3)
        root.addWidget(card)

        # --- кнопки
        btns = QHBoxLayout()
        self.start_btn = PrimaryPushButton(FluentIcon.PLAY, "Запустить")
        self.start_btn.clicked.connect(self.toggle)
        self.fw_btn = PushButton(FluentIcon.CERTIFICATE, "Разрешить в брандмауэре")
        self.fw_btn.clicked.connect(self.open_firewall)
        btns.addWidget(self.start_btn)
        btns.addWidget(self.fw_btn)
        btns.addStretch(1)
        root.addLayout(btns)

        # --- журнал
        root.addWidget(CaptionLabel("Журнал"))
        self.log = PlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Cascadia Mono", 9))
        root.addWidget(self.log, 1)

        self._set_status("stopped")

    # --------------------------------------------------------------- статус
    def _set_status(self, state, detail=None):
        presets = {
            "stopped": (FluentIcon.PHONE, "Остановлено", "Нажмите «Запустить»"),
            "waiting": (FluentIcon.WIFI, "Ожидание iPhone",
                        "На iPhone: Повтор экрана → выберите «%s»" % self.name_edit.text()),
            "connected": (FluentIcon.VIDEO, "iPhone подключён", "Идёт трансляция экрана"),
            "error": (FluentIcon.INFO, "Ошибка", ""),
        }
        icon, title, text = presets[state]
        self.status_icon.setIcon(icon)
        self.status_title.setText(title)
        self.status_text.setText(detail or text)
        if hasattr(self, "tray"):
            self.tray.setToolTip(f"Повтор экрана iPhone: {title}")
            self._update_tray_state()

    def _toast(self, title, text, error=False):
        (InfoBar.error if error else InfoBar.success)(
            title, text, duration=6000, position=InfoBarPosition.TOP, parent=self
        )

    # ------------------------------------------------------------- запуск
    def _auto_start(self):
        if self.auto_switch.isChecked():
            self.start()

    def toggle(self):
        if self.proc:
            self.stop()
        else:
            self.start()

    def start(self):
        if not self.uxplay:
            self._set_status("error", "Не найден uxplay.exe")
            self._append("uxplay.exe не найден. Положите его рядом со скриптом.\n"
                         "Страница проекта: " + UXPLAY_REPO)
            self._toast("Нужен uxplay.exe",
                        "Положите его рядом со скриптом — открываю страницу проекта.", True)
            QDesktopServices.openUrl(QUrl(UXPLAY_REPO))
            return
        if not bonjour_installed():
            # UxPlay 1.74+ имеет встроенный mDNS, Bonjour нужен только для старых сборок
            self._append("(Bonjour не найден — это нормально для UxPlay 1.74+. "
                         "Если iPhone не видит ПК, установите Bonjour.)")

        name = self.name_edit.text().strip() or "Windows PC"
        args = ["-n", name, "-nh"]
        if self.fs_switch.isChecked():
            args.append("-fs")
        if self.ll_switch.isChecked():
            args += ["-vsync", "no"]          # не ждать синхронизации - меньше задержка
        if self.fps_switch.isChecked():
            args += ["-fps", "60"]
        q = self.quality.currentIndex()
        if q == 1:
            args += ["-s", "2560x1440"]
        elif q == 2:
            args += ["-h265"]

        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(os.path.dirname(self.uxplay))
        env = QProcessEnvironment.systemEnvironment()
        exe_dir = os.path.dirname(self.uxplay)
        dirs = [exe_dir]
        plugins = os.path.join(exe_dir, "lib", "gstreamer-1.0")
        if os.path.isdir(plugins):          # портативная папка (make_portable.py)
            env.insert("GST_PLUGIN_PATH", plugins)
            env.insert("GST_PLUGIN_SYSTEM_PATH_1_0", plugins)
            env.insert("GST_REGISTRY_1_0", os.path.join(exe_dir, "registry.bin"))
        elif msys_bin_dir():                # запасной вариант: DLL и плагины из MSYS2
            dirs.append(msys_bin_dir())
        env.insert("PATH", os.pathsep.join(dirs + [env.value("PATH")]))
        self.proc.setProcessEnvironment(env)
        try:  # не показывать чёрное окно консоли
            self.proc.setCreateProcessArgumentsModifier(
                lambda args: setattr(args, "flags", args.flags | 0x08000000))
        except Exception:
            pass
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._on_output)
        self.proc.finished.connect(self._on_finished)
        self.proc.start(self.uxplay, args)

        self.start_btn.setText("Остановить")
        self.start_btn.setIcon(FluentIcon.PAUSE)
        self._lock(True)
        self._append(f"> uxplay {' '.join(args)}")
        self._set_status("waiting")

    # ------------------------------------------------------------------ трей
    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self.app_icon, self)
        self.tray.setToolTip("Повтор экрана iPhone")
        menu = QMenu()
        self.act_open = QAction("Открыть", self)
        self.act_open.triggered.connect(self.show_from_tray)
        self.act_toggle = QAction("Остановить приём", self)
        self.act_toggle.triggered.connect(self.toggle)
        self.act_autostart = QAction("Запускать вместе с Windows", self)
        self.act_autostart.setCheckable(True)
        self.act_autostart.triggered.connect(self._on_autostart_action)
        act_quit = QAction("Выход", self)
        act_quit.triggered.connect(self.quit_app)
        for a in (self.act_open, self.act_toggle):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(self.act_autostart)
        menu.addSeparator()
        menu.addAction(act_quit)
        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_from_tray()
            if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                          QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray.show()

    def show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        self._quitting = True
        self.stop()
        self.tray.hide()
        QApplication.quit()

    def _sync_autostart_ui(self):
        on = is_autostart()
        for w in (self.autostart_switch,):
            w.blockSignals(True)
            w.setChecked(on)
            w.blockSignals(False)
        self.act_autostart.setChecked(on)

    def _apply_autostart(self, on):
        try:
            set_autostart(on)
        except OSError as e:
            self._toast("Автозапуск", f"Не удалось изменить: {e}", True)
        self._sync_autostart_ui()

    def _on_autostart_switch(self, checked):
        self._apply_autostart(checked)

    def _on_autostart_action(self, checked):
        self._apply_autostart(checked)

    def _update_tray_state(self):
        running = self.proc is not None
        self.act_toggle.setText("Остановить приём" if running else "Запустить приём")

    def _keep_tick(self):
        if self.proc and self.keep_switch.isChecked():
            try:
                restore_minimized_windows_of(int(self.proc.processId()))
            except Exception:
                pass

    def _lock(self, on):
        for w in (self.name_edit, self.ll_switch, self.fps_switch,
                  self.quality, self.fs_switch):
            w.setEnabled(not on)

    def stop(self):
        if self.proc:
            self.proc.finished.disconnect(self._on_finished)
            self.proc.terminate()
            if not self.proc.waitForFinished(1500):
                self.proc.kill()
            self.proc = None
        self.start_btn.setText("Запустить")
        self.start_btn.setIcon(FluentIcon.PLAY)
        self._lock(False)
        self._set_status("stopped")

    # ---------------------------------------------------------- события
    def _on_output(self):
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        for line in data.splitlines():
            if not line.strip():
                continue
            self._append(line)
            low = line.lower()
            if "initialized server socket" in low:
                self._retries = 0
            if "accepted" in low and "connection" in low or "begin streaming" in low:
                self._set_status("connected")
            elif "connection closed" in low or "stop" in low and "stream" in low \
                    or "client disconnected" in low:
                self._set_status("waiting")

    def _on_finished(self, code, _status):
        self._append(f"uxplay завершился (код {code})")
        if code == -1073741515:
            self._append("Не найдены DLL для uxplay.exe. Запустите make_portable.py "
                         "(соберёт всё нужное рядом со скриптом) или установите MSYS2.")
        self.proc = None
        self.start_btn.setText("Запустить")
        self.start_btn.setIcon(FluentIcon.PLAY)
        self._lock(False)
        self._set_status("error" if code else "stopped",
                         "Процесс завершился с ошибкой — см. журнал" if code else None)
        # после включения ПК сеть может быть ещё не готова - пробуем снова (до 5 раз)
        if code and not self._quitting and self.auto_switch.isChecked() and self._retries < 5:
            self._retries += 1
            self._append(f"Повторный запуск через 10 с (попытка {self._retries}/5)...")
            QTimer.singleShot(10000, self._retry_start)

    def _retry_start(self):
        if self.proc is None and not self._quitting:
            self.start()

    def open_firewall(self):
        if not self.uxplay:
            self._toast("Сначала найдите uxplay.exe", "Правило создаётся для него.", True)
            return
        exe = self.uxplay.replace("'", "''")
        cmd = (
            f"Remove-NetFirewallRule -DisplayName 'UxPlay*' -ErrorAction SilentlyContinue; "
            f"New-NetFirewallRule -DisplayName 'UxPlay' -Direction Inbound -Program '{exe}' "
            f"-Action Allow -Profile Any -Protocol Any | Out-Null; "
            f"New-NetFirewallRule -DisplayName 'UxPlay mDNS' -Direction Inbound -Protocol UDP "
            f"-LocalPort 5353 -Action Allow -Profile Any | Out-Null; "
            f"Get-NetConnectionProfile | Where-Object {{ $_.NetworkCategory -eq 'Public' }} | "
            f"Set-NetConnectionProfile -NetworkCategory Private; "
            f"Write-Host 'Firewall rules added, network profile set to Private.'; "
            f"Read-Host 'Done. Press Enter'"
        )
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "powershell.exe",
            f'-NoProfile -ExecutionPolicy Bypass -Command "{cmd}"', None, 1)
        self._toast("Брандмауэр",
                    "Подтвердите запрос администратора, дождитесь «Done» и перезапустите приём.")

    def _append(self, text):
        self.log.appendPlainText(text)

    def closeEvent(self, e):
        # выключение/выход из Windows и пункт «Выход» в трее - закрываемся по-настоящему
        if self._quitting or QApplication.instance().isSavingSession():
            self.stop()
            e.accept()
            return
        e.ignore()
        self.hide()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.showMessage("Повтор экрана iPhone",
                                  "Приложение работает в трее. Выход - через значок в трее.",
                                  QSystemTrayIcon.MessageIcon.Information, 4000)


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)   # закрытие окна = в трей
    setTheme(Theme.AUTO)

    # один экземпляр: повторный запуск просто показывает уже работающее окно
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE_KEY)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.waitForBytesWritten(300)
        return
    QLocalServer.removeServer(INSTANCE_KEY)
    server = QLocalServer()
    server.listen(INSTANCE_KEY)

    w = MirrorWindow()
    server.newConnection.connect(lambda: (server.nextPendingConnection(), w.show_from_tray()))
    app.aboutToQuit.connect(w.stop)
    if "--tray" not in sys.argv:
        w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()