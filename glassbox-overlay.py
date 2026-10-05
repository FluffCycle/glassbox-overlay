#!/usr/bin/env python3
"""Glassbox Overlay: a transparent, click-through web overlay for Wayland.

Loads a browser-source style webpage (e.g. a Lumia Stream overlay) into a
fullscreen, always-on-top, fully transparent layer-shell surface that never
receives mouse or keyboard input. Anything the page draws (videos, alerts)
appears over the desktop; everything else is see-through.

Same approach as Discover: GTK3 + gtk-layer-shell + WebKit2GTK.
"""

import argparse
import ctypes.util
import os
import signal
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_URL_FILE = SCRIPT_DIR / "glassbox-overlay-source.txt"
TRAY_ICON = SCRIPT_DIR / "glassbox-overlay.svg"


def ensure_layer_shell_preloaded():
    """gtk-layer-shell >= 0.9 must be loaded before libwayland-client.

    From Python that can only be guaranteed with LD_PRELOAD, so re-exec
    ourselves with it set if it isn't already.
    """
    if os.environ.get("_GLASSBOX_PRELOADED") == "1":
        return
    lib = ctypes.util.find_library("gtk-layer-shell")
    if not lib:
        sys.exit(
            "gtk-layer-shell is not installed.\n"
            "Install it with:  sudo dnf install gtk-layer-shell"
        )
    env = dict(os.environ)
    env["LD_PRELOAD"] = " ".join(filter(None, [lib, env.get("LD_PRELOAD")]))
    env["_GLASSBOX_PRELOADED"] = "1"
    os.execve(sys.executable, [sys.executable, *sys.argv], env)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "url",
        nargs="?",
        help=f"Overlay URL (default: first line of {DEFAULT_URL_FILE.name})",
    )
    p.add_argument(
        "-m", "--monitor", type=int, default=None,
        help="Monitor index to show the overlay on (default: compositor's choice)",
    )
    p.add_argument(
        "--opacity", type=float, default=1.0,
        help="Overall overlay opacity, 0.0-1.0 (default: 1.0)",
    )
    p.add_argument(
        "--canvas-width", type=int, default=1920,
        help="Width the overlay page was designed for (your Lumia/OBS canvas "
             "width). The page is zoomed so this width fills the monitor, "
             "like an OBS browser source. 0 disables scaling. Default: 1920",
    )
    p.add_argument(
        "--no-tray", action="store_true",
        help="Don't show a system tray icon",
    )
    p.add_argument(
        "--debug", action="store_true",
        help="Draw a red border around the overlay and enable the web inspector",
    )
    return p.parse_args()


def read_url(args):
    if args.url:
        return args.url
    try:
        for line in DEFAULT_URL_FILE.read_text().splitlines():
            if line.strip():
                return line.strip()
    except FileNotFoundError:
        pass
    sys.exit(f"No URL given and {DEFAULT_URL_FILE} is missing or empty.")


def create_tray_icon(gi, Gtk, on_reload, on_quit):
    """System tray icon (StatusNotifierItem) with Reload and Quit actions.

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

    icon = str(TRAY_ICON) if TRAY_ICON.exists() else "video-display"
    indicator = AppIndicator.Indicator.new(
        "glassbox-overlay", icon,
        AppIndicator.IndicatorCategory.APPLICATION_STATUS)
    indicator.set_title("Glassbox Overlay")
    indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

    menu = Gtk.Menu()
    title = Gtk.MenuItem(label="Glassbox Overlay")
    title.set_sensitive(False)
    reload_item = Gtk.MenuItem(label="Reload overlay")
    reload_item.connect("activate", lambda _: on_reload())
    quit_item = Gtk.MenuItem(label="Quit")
    quit_item.connect("activate", lambda _: on_quit())
    for item in (title, Gtk.SeparatorMenuItem(), reload_item, quit_item):
        menu.append(item)
    menu.show_all()
    indicator.set_menu(menu)
    return indicator


def main():
    ensure_layer_shell_preloaded()
    args = parse_args()
    url = read_url(args)

    import gi
    gi.require_version("GtkLayerShell", "0.1")
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("WebKit2", "4.1")
    import cairo
    from gi.repository import Gdk, GLib, Gtk, GtkLayerShell, WebKit2

    if not GtkLayerShell.is_supported():
        sys.exit(
            "The compositor does not support wlr-layer-shell "
            "(are you running a Wayland session?)."
        )

    window = Gtk.Window(title="Glassbox Overlay")
    window.set_app_paintable(True)
    screen = window.get_screen()
    visual = screen.get_rgba_visual()
    if visual is None:
        sys.exit("No RGBA visual available; transparency is not possible.")
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
            sys.exit(f"Monitor {args.monitor} not found "
                     f"({display.get_n_monitors()} available).")
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
            gi, Gtk,
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
