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

Tested on Fedora with KDE Plasma 6.

## Installation

Install the dependencies for your distribution.

**Fedora**

```sh
sudo dnf install gtk-layer-shell webkit2gtk4.1 python3-gobject python3-cairo \
    gstreamer1-plugins-good
```

Fedora's own repositories can't play every video format. If an alert video
doesn't play, enable [RPM Fusion](https://rpmfusion.org/Configuration) and
install `gstreamer1-plugin-libav`.

**Arch Linux**

```sh
sudo pacman -S gtk-layer-shell webkit2gtk-4.1 python-gobject python-cairo \
    gst-plugins-good gst-libav
```

**Debian / Ubuntu**

```sh
sudo apt install gir1.2-gtklayershell-0.1 gir1.2-webkit2-4.1 python3-gi \
    python3-gi-cairo gstreamer1.0-plugins-good gstreamer1.0-libav
```

Then get the code:

```sh
git clone https://github.com/FluffCycle/glassbox-overlay.git
cd glassbox-overlay
```

## Usage

Pass the overlay URL on the command line:

```sh
./glassbox-overlay.py https://example.com/your-overlay-url
```

Or put it on the first line of a file named `glassbox-overlay-source.txt`, next to
`glassbox-overlay.py`, and run the script with no arguments:

```sh
echo "https://example.com/your-overlay-url" > glassbox-overlay-source.txt
./glassbox-overlay.py
```

Treat your overlay URL like a password: anyone who has it can see your alerts.
Don't commit `glassbox-overlay-source.txt` to a public repository.

### Options

| Option | Description |
| --- | --- |
| `-m N`, `--monitor N` | Show the overlay on monitor `N` (0, 1, ...). By default it uses the primary monitor. |
| `--canvas-width W` | The width your overlay page was designed for, i.e. your OBS or overlay-tool canvas width. The page is scaled so that width fills the monitor. Default: `1920`. `0` turns off scaling. |
| `--opacity X` | Opacity of the whole overlay, from `0.0` to `1.0`. Default: `1.0`. |
| `--debug` | Draw a red border around the overlay so you can see where it is, and enable the WebKit inspector. |

### Stopping it

The overlay never takes input, so you can't close it by clicking. Press Ctrl+C
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
- **"gtk-layer-shell is not installed":** install the gtk-layer-shell package
  for your distribution (see [Installation](#installation)).
- **"The compositor does not support wlr-layer-shell":** you're on GNOME or an
  X11 session. Neither is supported.
- **The overlay is on the wrong screen:** use `--monitor N`.
- **Alerts are the wrong size or in the wrong place:** set `--canvas-width` to
  the canvas width your overlay was designed for.
- **The alert shows up but the video doesn't play:** you're missing a GStreamer
  codec. See the notes under [Installation](#installation).

## License

[MIT](LICENSE)
