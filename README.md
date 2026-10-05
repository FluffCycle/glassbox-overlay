# Glassbox Overlay

A transparent, click-through, always-on-top overlay for Linux on Wayland.

Give it the URL of a browser-source overlay page, the kind you'd normally add to
OBS from Lumia Stream, StreamElements or Streamlabs, and it draws that page over
your whole screen. Alerts and videos the page plays show up on top of everything,
including fullscreen apps. The rest of the screen stays see-through, and mouse
and keyboard input passes straight through to whatever is underneath.

It's useful when you want stream alerts, such as channel point redemptions, to
show up on your own screen as well as on stream.

## Requirements

- A Wayland compositor that supports the `wlr-layer-shell` protocol: KDE Plasma
  6, Sway, Hyprland, river, Wayfire and others. GNOME doesn't support it.
- Python 3 with PyGObject and pycairo
- GTK 3, [gtk-layer-shell](https://github.com/wmww/gtk-layer-shell) and
  WebKit2GTK 4.1
- GStreamer plugins for the video formats your overlay uses (usually H.264
  and/or VP9)
- Optional: libayatana-appindicator (or the older libappindicator) for the
  system tray icon. Without it, the overlay still runs, just with no tray icon.

Tested on Fedora with KDE Plasma 6.

## Installation

Install the dependencies for your distribution.

**Fedora**

```sh
sudo dnf install gtk-layer-shell webkit2gtk4.1 python3-gobject python3-cairo \
    gstreamer1-plugins-good libayatana-appindicator-gtk3
```

Fedora's own repositories can't play every video format. If an alert video
doesn't play, enable [RPM Fusion](https://rpmfusion.org/Configuration) and
install `gstreamer1-plugin-libav`.

**Arch Linux**

```sh
sudo pacman -S gtk-layer-shell webkit2gtk-4.1 python-gobject python-cairo \
    gst-plugins-good gst-libav libayatana-appindicator
```

**Debian / Ubuntu**

```sh
sudo apt install gir1.2-gtklayershell-0.1 gir1.2-webkit2-4.1 python3-gi \
    python3-gi-cairo gstreamer1.0-plugins-good gstreamer1.0-libav \
    gir1.2-ayatanaappindicator3-0.1
```

Then get the code:

```sh
git clone https://github.com/FluffCycle/glassbox-overlay.git
cd glassbox-overlay
```

## Usage

1. Put your overlay URL on the first line of a file named
   `glassbox-overlay-source.txt`, next to `glassbox-overlay.py`:

   ```sh
   echo "https://example.com/your-overlay-url" > glassbox-overlay-source.txt
   ```

   Treat your overlay URL like a password: anyone who has it can see your
   alerts. The included `.gitignore` keeps this file out of git so you don't
   publish it by accident.

2. Optionally, change the settings in `glassbox-overlay.conf` (see
   [Settings](#settings)).

3. Start Glassbox Overlay by double-clicking `glassbox-overlay.py` in your file
   manager. In Dolphin, choose **Execute** when it asks what to do with the
   file. Or run it from a terminal:

   ```sh
   ./glassbox-overlay.py
   ```

If something goes wrong at startup, such as a missing URL or a bad setting,
Glassbox Overlay shows an error dialog explaining why. When it's run from a
terminal, it prints the error there instead.

### Settings

Settings live in `glassbox-overlay.conf`, next to `glassbox-overlay.py`. Open
it in any text editor, change a value, save, and restart Glassbox Overlay.
Each setting is explained in the file.

You can also override any setting for a single run with a command-line option.
Command-line options always win over the config file. For example, to
try a different URL on monitor 1:

```sh
./glassbox-overlay.py --monitor 1 https://example.com/another-overlay-url
```

| Config setting | Command-line option | Description |
| --- | --- | --- |
| `monitor` | `-m N`, `--monitor N` | Show the overlay on monitor `N` (0, 1, ...). Blank or unset uses the primary monitor. |
| `canvas_width` | `--canvas-width W` | The width your overlay page was designed for, i.e. your OBS or overlay-tool canvas width. The page is scaled so that width fills the monitor. Default: `1920`. `0` turns off scaling. |
| `opacity` | `--opacity X` | Opacity of the whole overlay, from `0.0` to `1.0`. Default: `1.0`. |
| `tray` | `--no-tray` | Show the system tray icon. Default: `true`. |
| `debug` | `--debug` | Draw a red border around the overlay so you can see where it is, and enable the WebKit inspector. Default: `false`. |
| — | `url` (first argument) | Use this overlay URL instead of the one in `glassbox-overlay-source.txt`. |

### Tray icon and stopping it

The overlay never takes input, so you can't close it by clicking on it.
Instead, while it's running, a Glassbox Overlay icon appears in your system
tray. Click or right-click it for:

- **Reload overlay:** reload the page, e.g. after changing the overlay in your
  streaming tool.
- **Quit:** close Glassbox Overlay.

Without the tray icon (no AppIndicator library, or `--no-tray`), press Ctrl+C
in the terminal it's running in, or run:

```sh
pkill -f glassbox-overlay.py
```

If the page fails to load or WebKit's renderer crashes, the page reloads
automatically.

### Start at login

- **KDE Plasma:** open System Settings → Autostart → Add… → Add Application,
  and enter the full path to `glassbox-overlay.py` as the command.
- **Other desktops:** run `/path/to/glassbox-overlay.py` from your compositor's startup
  config, for example `exec` in Sway or `exec-once` in Hyprland.

## How it works

- The window is a [layer-shell](https://wayland.app/protocols/wlr-layer-shell-unstable-v1)
  surface on the `overlay` layer, so it sits above normal and fullscreen
  windows. It's anchored to every edge of the monitor and ignores panels.
- It asks for no keyboard focus, and its input region is empty, so clicks and
  key presses go to the windows underneath.
- The page is loaded in WebKit2GTK with a transparent background (enforced with
  a user stylesheet) and autoplay allowed, so videos play with sound without
  anyone clicking the page first.

## Troubleshooting

- **Nothing shows up, or the screen goes black:** try
  `WEBKIT_DISABLE_DMABUF_RENDERER=1 ./glassbox-overlay.py`.
- **No tray icon:** install the AppIndicator package listed for your
  distribution under [Installation](#installation). The terminal shows "No
  AppIndicator library found" when it's missing.
- **"gtk-layer-shell is not installed":** install the gtk-layer-shell package
  for your distribution (see [Installation](#installation)).
- **"The compositor does not support wlr-layer-shell":** you're on GNOME or an
  X11 session. Neither is supported.
- **Double-clicking opens the script in a text editor instead of running it:**
  make sure it's executable (`chmod +x glassbox-overlay.py`, or Properties →
  Permissions → "Is executable" in Dolphin).
- **The overlay is on the wrong screen:** set `monitor` in
  `glassbox-overlay.conf`.
- **Alerts are the wrong size or in the wrong place:** set `canvas_width` in
  `glassbox-overlay.conf` to the canvas width your overlay was designed for.
- **The alert shows up but the video doesn't play:** you're missing a GStreamer
  codec. See the notes under [Installation](#installation).

## License

[MIT](LICENSE)
