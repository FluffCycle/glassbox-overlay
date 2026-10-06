"""Glassbox Overlay: a transparent, click-through web overlay for Wayland.

Loads a browser-source style webpage (e.g. a Lumia Stream overlay) into a
fullscreen, always-on-top, fully transparent layer-shell surface that never
receives mouse or keyboard input. Anything the page draws (videos, alerts)
appears over the desktop; everything else is see-through.

Same approach as Discover: GTK3 + gtk-layer-shell + WebKit2GTK.
"""

import argparse
import configparser
import ctypes.util
import fcntl
import importlib.resources
import os
import re
import shutil
import signal
import sys
import tempfile
from pathlib import Path

APP_ID = "glassbox-overlay"
PACKAGE_FILES = importlib.resources.files(__package__)
CONFIG_TEMPLATE = PACKAGE_FILES / f"{APP_ID}.conf"
DESKTOP_TEMPLATE = PACKAGE_FILES / f"{APP_ID}.desktop"
TRAY_ICON = PACKAGE_FILES / f"{APP_ID}.svg"

CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME")
                 or Path.home() / ".local" / "share")
DEFAULT_CONFIG_FILE = CONFIG_HOME / APP_ID / f"{APP_ID}.conf"
DESKTOP_ENTRY = DATA_HOME / "applications" / f"{APP_ID}.desktop"
INSTALLED_ICON = DATA_HOME / "icons/hicolor/scalable/apps" / f"{APP_ID}.svg"
AUTOSTART_ENTRY = CONFIG_HOME / "autostart" / f"{APP_ID}.desktop"

INSTALL_COMMAND = ("pipx install --force --system-site-packages "
                   "git+https://github.com/FluffCycle/glassbox-overlay.git")
URL_SCHEMES = ("http://", "https://", "file://")


def show_dialog(title, message, error=True):
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        dialog = Gtk.MessageDialog(
            message_type=(Gtk.MessageType.ERROR if error
                          else Gtk.MessageType.INFO),
            buttons=Gtk.ButtonsType.CLOSE,
            text=title,
        )
        dialog.set_title("Glassbox Overlay")
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()
    except Exception:
        pass


def fail(message):
    """Exit with an error, also shown in a dialog when there's no terminal
    to print it to (e.g. launched from the app launcher)."""
    print(message, file=sys.stderr)
    if not sys.stderr.isatty():
        show_dialog("Glassbox Overlay couldn't start", message)
    sys.exit(1)


def check_python_modules():
    """PyGObject and pycairo only come from the distro, so a pipx install
    without --system-site-packages can't see them."""
    try:
        import gi  # noqa: F401
        import cairo  # noqa: F401
    except ImportError as e:
        fail(f"The Python module '{e.name}' is missing. Install PyGObject "
             "and pycairo from your distribution (see the README). If you "
             "installed Glassbox Overlay with pipx, reinstall it with "
             f"--system-site-packages:\n\n    {INSTALL_COMMAND}")


def require_gi_versions():
    import gi
    missing = []
    for namespace, version, package in (
            ("Gtk", "3.0", "GTK 3"),
            ("Gdk", "3.0", "GTK 3"),
            ("GtkLayerShell", "0.1", "gtk-layer-shell"),
            ("WebKit2", "4.1", "WebKit2GTK 4.1")):
        try:
            gi.require_version(namespace, version)
        except ValueError:
            if package not in missing:
                missing.append(package)
    if missing:
        fail(f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} "
             "not installed. Install the packages for your distribution "
             "listed in the README.")


def ensure_single_instance(allow_multiple, config_file):
    """Exit with a pop-up if another instance is already running.

    Instances started with allow_multiple still take the lock when it's
    free, so a normal launch afterwards is still refused.

    Holds an exclusive lock on a per-user lock file for the life of the
    process. The kernel releases it when the process exits, even if it
    crashes, so a stale lock can never block a fresh start.
    """
    lock_dir = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    lock_path = Path(lock_dir) / f"glassbox-overlay-{os.getuid()}.lock"
    # The fallback is the shared temp dir, where another user could plant a
    # symlink at this name: don't follow it, and don't truncate what's there.
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    lock_file = os.fdopen(fd, "r+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        if allow_multiple:
            return None
        message = ("Glassbox Overlay is already running. To close it, use "
                   "Quit in its system tray icon.\n\nTo run more than one "
                   "overlay at a time, set allow_multiple_instances = true in "
                   f"{config_file}.")
        print(message, file=sys.stderr)
        show_dialog("Glassbox Overlay is already running", message,
                    error=False)
        sys.exit(1)
    return lock_file  # keep open: closing it releases the lock


def ensure_layer_shell_preloaded():
    """gtk-layer-shell >= 0.9 must be loaded before libwayland-client.

    From Python that can only be guaranteed with LD_PRELOAD, so re-exec
    ourselves with it set if it isn't already.
    """
    if os.environ.get("_GLASSBOX_PRELOADED") == "1":
        return
    lib = ctypes.util.find_library("gtk-layer-shell")
    if not lib:
        fail("gtk-layer-shell is not installed. Install the gtk-layer-shell "
             "package for your distribution (see the README).")
    env = dict(os.environ)
    env["LD_PRELOAD"] = " ".join(filter(None, [lib, env.get("LD_PRELOAD")]))
    env["_GLASSBOX_PRELOADED"] = "1"
    # orig_argv keeps "-m glassbox_overlay" when run that way.
    os.execve(sys.executable, [sys.executable, *sys.orig_argv[1:]], env)


# --- Config file -----------------------------------------------------------

def ensure_config(config_file):
    """Create the config file from the bundled template if it's missing."""
    if config_file.exists():
        return
    config_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Owner-only: it holds the overlay URL, which is as private as a
        # password.
        fd = os.open(config_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "w") as f:
        f.write(CONFIG_TEMPLATE.read_text())
    print(f"Created {config_file}", file=sys.stderr)


def load_config(config_file):
    """Read option defaults from the config file.

    Command-line arguments override anything set here.
    """
    # No interpolation: overlay URLs often contain '%'.
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(config_file)
    except configparser.Error as e:
        fail(f"Couldn't read {config_file}:\n{e}")
    if not parser.has_section("overlay"):
        return {}
    section = parser["overlay"]

    getters = {
        "url": section.get,
        "monitor": section.getint,
        "canvas_width": section.getint,
        "opacity": section.getfloat,
        "tray": section.getboolean,
        "debug": section.getboolean,
        "allow_multiple_instances": section.getboolean,
    }
    for key in section:
        if key not in getters:
            print(f"{config_file}: ignoring unknown option '{key}'",
                  file=sys.stderr)

    config = {}
    for key, get in getters.items():
        if not section.get(key, "").strip():
            continue  # unset or blank: keep the built-in default
        try:
            config[key] = get(key).strip() if key == "url" else get(key)
        except ValueError:
            fail(f"{config_file}: invalid value for '{key}': "
                 f"{section[key]!r}")
    if "tray" in config:
        config["no_tray"] = not config.pop("tray")
    return config


def save_url(config_file, url):
    """Set url in the config file, keeping its comments and layout."""
    text = config_file.read_text()
    line = f"url = {url}"
    text, n = re.subn(r"^url[ \t]*[=:].*$", lambda _: line, text,
                      count=1, flags=re.M)
    if not n:
        text, n = re.subn(r"^\[overlay\][ \t]*$",
                          lambda m: f"{m.group(0)}\n{line}", text,
                          count=1, flags=re.M)
    if not n:
        text = f"{text.rstrip()}\n\n[overlay]\n{line}\n".lstrip()
    config_file.write_text(text)


def ask_for_url(config_file):
    """Ask for the overlay URL in a dialog. Returns None if cancelled."""
    from gi.repository import Gtk
    dialog = Gtk.Dialog(title="Glassbox Overlay")
    dialog.add_buttons("_Cancel", Gtk.ResponseType.CANCEL,
                       "_Save and Start", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)

    box = dialog.get_content_area()
    box.set_spacing(10)
    box.set_border_width(12)
    intro = Gtk.Label(
        label="Paste the URL of your overlay: the browser-source link you'd "
              "add to OBS from Lumia Stream, StreamElements or Streamlabs.",
        wrap=True, xalign=0, max_width_chars=60)
    entry = Gtk.Entry(activates_default=True, width_chars=60,
                      placeholder_text="https://")
    note = Gtk.Label(
        label=f"It's saved in {config_file}, where you can change it later. "
              "Keep it private: anyone who has it can see your alerts.",
        wrap=True, xalign=0, max_width_chars=60)
    note.get_style_context().add_class("dim-label")
    for widget in (intro, entry, note):
        box.add(widget)

    save_button = dialog.get_widget_for_response(Gtk.ResponseType.OK)

    def validate(_entry):
        save_button.set_sensitive(
            entry.get_text().strip().startswith(URL_SCHEMES))

    entry.connect("changed", validate)
    validate(entry)

    dialog.show_all()
    response = dialog.run()
    url = entry.get_text().strip()
    dialog.destroy()
    return url if response == Gtk.ResponseType.OK else None


def read_url(args):
    if args.url:
        return args.url
    url = ask_for_url(args.config)
    if not url:
        print(f"No overlay URL set. Add it to {args.config} or pass it on "
              "the command line.", file=sys.stderr)
        sys.exit(1)
    save_url(args.config, url)
    return url


# --- App launcher and autostart entries -------------------------------------

def exec_arg(arg):
    """Quote one argument for a desktop entry's Exec= key."""
    arg = arg.replace("%", "%%")
    if re.fullmatch(r"[\w@%+=:,./-]+", arg):
        return arg
    return '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"'


def write_desktop_entry(path, config_file):
    # Launchers and autostart may not have ~/.local/bin on PATH, so point
    # Exec at this command's full path.
    command = [shutil.which(APP_ID) or os.path.abspath(sys.argv[0])]
    if config_file != DEFAULT_CONFIG_FILE:
        command += ["--config", str(config_file)]
    exec_line = "Exec=" + " ".join(map(exec_arg, command))
    entry = re.sub(r"^Exec=.*$", lambda _: exec_line,
                   DESKTOP_TEMPLATE.read_text(), flags=re.M)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(entry)
    print(f"Installed {path}")


def remove_file(path):
    if path.exists():
        path.unlink()
        print(f"Removed {path}")
    else:
        print(f"Nothing to remove at {path}")


def install_desktop_entry(config_file):
    INSTALLED_ICON.parent.mkdir(parents=True, exist_ok=True)
    INSTALLED_ICON.write_bytes(TRAY_ICON.read_bytes())
    print(f"Installed {INSTALLED_ICON}")
    write_desktop_entry(DESKTOP_ENTRY, config_file)


def remove_desktop_entry():
    remove_file(DESKTOP_ENTRY)
    remove_file(INSTALLED_ICON)


def enable_autostart(config_file):
    write_desktop_entry(AUTOSTART_ENTRY, config_file)


def disable_autostart():
    remove_file(AUTOSTART_ENTRY)


# --- Command line ------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=f"Settings are read from {DEFAULT_CONFIG_FILE} (or --config); "
               "command-line options override them.",
    )
    p.add_argument(
        "url",
        nargs="?",
        help="Overlay URL (default: url in the config file; if that's blank "
             "too, you're asked for it)",
    )
    p.add_argument(
        "-c", "--config", metavar="FILE", default=DEFAULT_CONFIG_FILE,
        type=lambda s: Path(s).expanduser().absolute(),
        help="Use this config file instead of the default one. It's created "
             "if it doesn't exist. Handy for running several overlays.",
    )
    p.add_argument(
        "-m", "--monitor", type=int, default=None,
        help="Monitor index to show the overlay on (default: primary monitor)",
    )
    p.add_argument(
        "--opacity", type=float, default=1.0,
        help="Overall overlay opacity, 0.0-1.0 (default: %(default)s)",
    )
    p.add_argument(
        "--canvas-width", type=int, default=1920,
        help="Width the overlay page was designed for (your Lumia/OBS canvas "
             "width). The page is zoomed so this width fills the monitor, "
             "like an OBS browser source. 0 disables scaling. "
             "(default: %(default)s)",
    )
    p.add_argument(
        "--no-tray", action="store_true",
        help="Don't show a system tray icon",
    )
    p.add_argument(
        "--debug", action="store_true",
        help="Draw a red border around the overlay and enable the web inspector",
    )
    p.add_argument(
        "--allow-multiple-instances", action="store_true",
        help="Allow this instance to run alongside others (by default only "
             "one Glassbox Overlay can run at a time)",
    )
    setup = p.add_argument_group(
        "setup", "Do one of these and exit, instead of starting the overlay.")
    actions = setup.add_mutually_exclusive_group()
    actions.add_argument(
        "--install-desktop-entry", action="store_true",
        help="Add Glassbox Overlay to the app launcher",
    )
    actions.add_argument(
        "--remove-desktop-entry", action="store_true",
        help="Remove it from the app launcher",
    )
    actions.add_argument(
        "--enable-autostart", action="store_true",
        help="Start Glassbox Overlay when you log in",
    )
    actions.add_argument(
        "--disable-autostart", action="store_true",
        help="Stop starting it when you log in",
    )
    return p


def create_tray_icon(gi, Gtk, config_file, on_reload, on_quit):
    """System tray icon (StatusNotifierItem) with Reload, Open Settings,
    Start at Login and Quit.

    Returns None if no AppIndicator library is installed; the overlay works
    fine without it.
    """
    AppIndicator = None
    for namespace in ("AyatanaAppIndicator3", "AppIndicator3"):
        try:
            gi.require_version(namespace, "0.1")
            AppIndicator = getattr(
                __import__("gi.repository", fromlist=[namespace]), namespace)
            break
        except (ImportError, ValueError):
            continue
    if AppIndicator is None:
        print("No AppIndicator library found; running without a tray icon.",
              file=sys.stderr)
        return None
    from gi.repository import Gio, GLib

    icon = str(TRAY_ICON) if TRAY_ICON.is_file() else "video-display"
    indicator = AppIndicator.Indicator.new(
        "glassbox-overlay", icon,
        AppIndicator.IndicatorCategory.APPLICATION_STATUS)
    indicator.set_title("Glassbox Overlay")
    indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

    def open_settings():
        try:
            Gio.AppInfo.launch_default_for_uri(config_file.as_uri(), None)
        except GLib.Error as e:
            show_dialog("Couldn't open the settings file",
                        f"{config_file}\n\n{e.message}")

    def toggle_autostart(item):
        if item.get_active():
            enable_autostart(config_file)
        else:
            disable_autostart()

    menu = Gtk.Menu()
    title = Gtk.MenuItem(label="Glassbox Overlay")
    title.set_sensitive(False)
    reload_item = Gtk.MenuItem(label="Reload overlay")
    reload_item.connect("activate", lambda _: on_reload())
    settings_item = Gtk.MenuItem(label="Open settings")
    settings_item.connect("activate", lambda _: open_settings())
    autostart_item = Gtk.CheckMenuItem(label="Start at login")
    autostart_item.set_active(AUTOSTART_ENTRY.exists())
    autostart_item.connect("toggled", toggle_autostart)
    quit_item = Gtk.MenuItem(label="Quit")
    quit_item.connect("activate", lambda _: on_quit())
    for item in (title, Gtk.SeparatorMenuItem(), reload_item, settings_item,
                 autostart_item, Gtk.SeparatorMenuItem(), quit_item):
        menu.append(item)
    menu.show_all()
    indicator.set_menu(menu)
    return indicator


def main():
    check_python_modules()
    parser = build_parser()
    args = parser.parse_args()
    if args.install_desktop_entry:
        return install_desktop_entry(args.config)
    if args.remove_desktop_entry:
        return remove_desktop_entry()
    if args.enable_autostart:
        return enable_autostart(args.config)
    if args.disable_autostart:
        return disable_autostart()

    ensure_layer_shell_preloaded()
    require_gi_versions()
    # The Wayland app ID, which the desktop uses to find the launcher entry
    # and its icon for our dialogs.
    from gi.repository import GLib
    GLib.set_prgname(APP_ID)
    ensure_config(args.config)
    parser.set_defaults(**load_config(args.config))
    args = parser.parse_args()
    # Held for the life of the process; see ensure_single_instance().
    instance_lock = ensure_single_instance(args.allow_multiple_instances,
                                           args.config)
    url = read_url(args)

    import gi
    import cairo
    from gi.repository import Gdk, GLib, Gtk, GtkLayerShell, WebKit2

    if not GtkLayerShell.is_supported():
        fail("Your desktop doesn't support the wlr-layer-shell protocol. "
             "Glassbox Overlay needs a Wayland session on a desktop such as "
             "KDE Plasma, Sway or Hyprland (GNOME isn't supported).")

    window = Gtk.Window(title="Glassbox Overlay")
    window.set_app_paintable(True)
    screen = window.get_screen()
    visual = screen.get_rgba_visual()
    if visual is None:
        fail("No RGBA visual available; transparency is not possible.")
    window.set_visual(visual)

    # Layer-shell: topmost layer, cover the whole output, ignore panels,
    # never take keyboard focus.
    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_namespace(window, "glassbox-overlay")
    GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
    for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                 GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
        GtkLayerShell.set_anchor(window, edge, True)
    GtkLayerShell.set_exclusive_zone(window, -1)
    GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.NONE)

    display = Gdk.Display.get_default()
    if args.monitor is not None:
        monitor = display.get_monitor(args.monitor)
        if monitor is None:
            fail(f"Monitor {args.monitor} not found "
                 f"({display.get_n_monitors()} available, numbered from 0).")
    else:
        monitor = display.get_primary_monitor() or display.get_monitor(0)
    GtkLayerShell.set_monitor(window, monitor)

    # Web view with a transparent background and unrestricted autoplay
    # (alerts have to play with sound without anyone clicking the page).
    css = ("html, body { background: transparent !important; "
           "overflow: hidden !important; }\n"
           f"html {{ opacity: {max(0.0, min(1.0, args.opacity))} !important; }}\n")
    if args.debug:
        css += "html { box-shadow: inset 0 0 0 6px red !important; }\n"
    content = WebKit2.UserContentManager()
    content.add_style_sheet(WebKit2.UserStyleSheet(
        css,
        WebKit2.UserContentInjectedFrames.ALL_FRAMES,
        WebKit2.UserStyleLevel.USER,
        None, None,
    ))
    policies = WebKit2.WebsitePolicies(autoplay=WebKit2.AutoplayPolicy.ALLOW)
    webview = WebKit2.WebView(
        user_content_manager=content,
        website_policies=policies,
    )
    settings = webview.get_settings()
    settings.set_media_playback_requires_user_gesture(False)
    settings.set_enable_webgl(True)
    settings.set_enable_developer_extras(args.debug)
    settings.set_hardware_acceleration_policy(
        WebKit2.HardwareAccelerationPolicy.ALWAYS)

    webview.set_background_color(Gdk.RGBA(0, 0, 0, 0))

    # Match an OBS browser source: a page laid out for an N-pixel-wide canvas
    # fills the monitor exactly.
    if args.canvas_width > 0:
        webview.set_zoom_level(
            monitor.get_geometry().width / args.canvas_width)

    # Reload automatically if the page fails to load or the renderer dies,
    # so a network blip doesn't silently kill the overlay mid-stream.
    retry = {"id": 0}

    def schedule_reload(delay_s=5):
        if retry["id"]:
            return
        def do_reload():
            retry["id"] = 0
            print(f"Reloading {url}", file=sys.stderr)
            webview.load_uri(url)
            return GLib.SOURCE_REMOVE
        retry["id"] = GLib.timeout_add_seconds(delay_s, do_reload)

    def on_load_failed(_view, _event, failing_uri, error):
        print(f"Load failed ({failing_uri}): {error.message}", file=sys.stderr)
        schedule_reload()
        return True  # suppress WebKit's error page

    def on_crash(_view, reason):
        print(f"Web process terminated ({reason.value_nick}); restarting",
              file=sys.stderr)
        schedule_reload(1)

    webview.connect("load-failed", on_load_failed)
    webview.connect("web-process-terminated", on_crash)
    # Never open popups or new windows.
    webview.connect("create", lambda *_: None)

    window.add(webview)

    def make_click_through(win):
        # Empty input region: all pointer/touch events fall through to the
        # windows underneath.
        win.input_shape_combine_region(cairo.Region())

    window.connect("realize", make_click_through)
    window.connect("destroy", Gtk.main_quit)

    try:
        gi.require_version("GLibUnix", "2.0")
        from gi.repository import GLibUnix
        signal_add = GLibUnix.signal_add
    except (ImportError, ValueError):
        signal_add = GLib.unix_signal_add
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal_add(GLib.PRIORITY_DEFAULT, sig, Gtk.main_quit)

    # Keep a reference so the tray icon isn't garbage collected.
    tray = None
    if not args.no_tray:
        tray = create_tray_icon(
            gi, Gtk, args.config,
            on_reload=lambda: webview.load_uri(url),
            on_quit=Gtk.main_quit,
        )

    window.show_all()
    # Re-apply after mapping in case GTK reset it while creating the surface.
    make_click_through(window)

    print(f"Overlay running: {url}", file=sys.stderr)
    webview.load_uri(url)
    Gtk.main()


if __name__ == "__main__":
    main()
