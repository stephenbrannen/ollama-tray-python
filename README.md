# ollama-tray-python

A system tray icon for Linux and the macOS menu bar that shows which models an Ollama server has loaded, and unloads them on demand. Licensed under the [MIT License](LICENSE).

Supported platforms:

* Linux on x86_64 and aarch64, on X11 or Wayland, with a desktop that shows tray icons. GNOME needs the AppIndicator extension.
* macOS, in the menu bar. The Nix package needs Apple silicon; the release wheel also runs on Intel Macs. See [Install on macOS](#install-on-macos).

## Icon colors

* Green: a model is loaded.
* Grey: the server answers but nothing is loaded. The icon is also grey for the moment before the first check returns.
* Red: the server cannot be reached or does not answer in time. Hover over the icon to see the reason.

## Menu

Right-click the icon.

* `Stop <model>`: unloads that model. One entry per loaded model.
* `Keep loaded` > `<model>`: keeps that model loaded until you stop it, without changing the server's keep-alive setting. Lists the models that are not already kept loaded, and is hidden when there are none.
* `Stop all`: unloads every loaded model, up to 8 at once. Shown when two or more are loaded.
* `Refresh now`: checks the server immediately.
* `Quit`: exits.

If a stop or a keep-loaded request fails, a notification shows the reason.

## Keeping models loaded

Ollama unloads a model after five minutes without a request, unless the server is told otherwise. To keep models loaded longer, set `OLLAMA_KEEP_ALIVE` on the server. On NixOS:

```nix
services.ollama.environmentVariables.OLLAMA_KEEP_ALIVE = "24h";
```

The tooltip shows how long each model has left, for example `llama3:8b (4.7 GB VRAM, unloads in 3h 12m)`.

* `unloads in <1m`: less than a minute left.
* `kept loaded`: the server never unloads the model on its own (`OLLAMA_KEEP_ALIVE=-1`).
* Nothing is shown when the server does not report a time the tray can read.

Use `Stop <model>` to unload a model before its time is up.

## Options

* `--host`: server address as `[http[s]://][host][:port][/path]`. Falls back to `OLLAMA_HOST`, then `http://127.0.0.1:11434`.
  * A missing scheme means `http`. Other schemes are rejected.
  * A missing host means `127.0.0.1`, not `localhost`, because `localhost` can resolve to `::1` while Ollama listens on IPv4.
  * A missing port means 11434 on every scheme, including `https`. Port 0 is rejected.
  * A path is kept without its trailing slash.
  * A user name, password, query or fragment is rejected.
* `--interval`: seconds between status checks, a finite number above 0. Defaults to 5.
* `--verbose`: log at INFO level, which adds a line when the server recovers.
* `--version`: print the version and exit.

Logs go to stderr. By default the tray logs a warning when the server becomes unreachable and when a stop fails.

## Install on NixOS

Try it without installing:

```sh
nix run github:stephenbrannen/ollama-tray-python
```

Nix downloads the prebuilt package from the public `ollama-tray` Cachix cache instead of compiling it. The first time, Nix asks whether to trust the cache settings in the flake. Answer `y`, or pass `--accept-flake-config` to skip the prompt.

Or add it to a flake-based system configuration:

```nix
# flake.nix
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    ollama-tray = {
      url = "github:stephenbrannen/ollama-tray-python";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { nixpkgs, ... }@inputs: {
    nixosConfigurations.my-host = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      # Passes `inputs` to every module, so configuration.nix can name `inputs.ollama-tray`.
      specialArgs = { inherit inputs; };
      modules = [ ./configuration.nix ];
    };
  };
}
```

```nix
# configuration.nix
{ pkgs, inputs, ... }:
{
  environment.systemPackages = [
    inputs.ollama-tray.packages.${pkgs.stdenv.hostPlatform.system}.default
  ];
}
```

* `nix run github:...` builds against the nixpkgs pinned in this repository and downloads its own copy of every dependency.
* `inputs.nixpkgs.follows = "nixpkgs"` makes the package share your system's nixpkgs, so it reuses your Python and Qt instead. It still compiles its own PySide6 (see below).
* For a one-off run with your own nixpkgs, use `nix run --override-input nixpkgs nixpkgs github:stephenbrannen/ollama-tray-python`.
* A shared nixpkgs must provide Python 3.13 and PySide6 6.x.
* To use the cache for every build on the system, add it to your NixOS configuration. Without this, a system rebuild compiles the package, because Nix ignores the cache settings of a flake used as an input:

  ```nix
  nix.settings = {
    substituters = [ "https://ollama-tray.cachix.org" ];
    trusted-public-keys = [ "ollama-tray.cachix.org-1:VsA+mvlRdugWzqzTzrRhqRpJ25Sc9tbL6z4or9oHc+U=" ];
  };
  ```

* The cache is public, so anyone can download its contents. It holds only `x86_64-linux` and `aarch64-darwin` builds made against this repository's pinned nixpkgs, so `aarch64-linux` and `inputs.nixpkgs.follows` builds still compile locally.
* The package builds its own PySide6 with only the Qt modules the app uses, which cuts the download from about 2.2 GiB to about 750 MiB. A build that misses the cache compiles PySide6, which takes 6 to 10 minutes on 4 cores. The build runs at most 4 compile jobs, because more can run out of memory on an 8 GiB machine.
* The Nix package uses the Wayland support built into Qt 6.11. It still needs a manual check on GNOME and KDE Wayland desktops.
* `nix flake update` refreshes the pinned nixpkgs.
* GNOME needs the AppIndicator extension to show tray icons.

## Install on macOS

The tray runs on Apple silicon Macs and shows its icon in the menu bar. It needs an Ollama server, on the same Mac or reachable over the network.

Try it with Nix:

```sh
nix run github:stephenbrannen/ollama-tray-python
```

As on Linux, Nix downloads the prebuilt package from the `ollama-tray` Cachix cache. The first time, Nix asks whether to trust the cache settings in the flake. Answer `y`, or pass `--accept-flake-config`. If your user is not a trusted Nix user, Nix ignores the cache and compiles PySide6 first, which takes a long time.

Or install the release wheel with uv. This needs no Nix, and it also works on Intel Macs, which the flake does not support:

```sh
gh release download <tag> -R stephenbrannen/ollama-tray-python -p '*.whl' -p checksums.txt
shasum -a 256 -c checksums.txt --ignore-missing
uv tool install ./ollama_tray-<version>-py3-none-any.whl
```

`uv tool install` puts `ollama-tray` in `~/.local/bin`. See [Install from a release](#install-from-a-release) for the provenance check.

### Start at login

Either add the program in System Settings > General > Login Items, or create a LaunchAgent. A LaunchAgent can pass options and restarts the tray if it crashes. Save this as `~/Library/LaunchAgents/io.github.stephenbrannen.ollama-tray.plist`, and replace `YOU` with your user name:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>io.github.stephenbrannen.ollama-tray</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOU/.local/bin/ollama-tray</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>StandardErrorPath</key>
  <string>/tmp/ollama-tray.log</string>
</dict>
</plist>
```

* launchd does not read your shell profile, so the path must be absolute. For a Nix profile install, use `/Users/YOU/.nix-profile/bin/ollama-tray`.
* `KeepAlive` restarts the tray after a crash but not after you pick `Quit`.
* Load it now with `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.github.stephenbrannen.ollama-tray.plist`. It also loads at every login.
* Remove it with `launchctl bootout gui/$(id -u)/io.github.stephenbrannen.ollama-tray`, then delete the file.

## Install from a release

Each tagged release has a source archive (`ollama_tray-<version>.tar.gz`), a wheel (`ollama_tray-<version>-py3-none-any.whl`) and a `checksums.txt`. The wheel needs Python 3.13 or newer and installs PySide6 from PyPI.

* Download the wheel and `checksums.txt` from the Releases page, or with `gh release download <tag> -R stephenbrannen/ollama-tray-python -p '*.whl' -p checksums.txt`.
* Verify it with `sha256sum -c checksums.txt --ignore-missing`.
* Verify its build provenance with `gh attestation verify <wheel> -R stephenbrannen/ollama-tray-python`. This proves the file came from this repository's release workflow. Releases get an attestation only while the repository is public.
* Install it with `uv tool install ./ollama_tray-<version>-py3-none-any.whl`, or with `pipx install ./ollama_tray-<version>-py3-none-any.whl`.
* On Linux, copy `ollama-tray.desktop` from the source archive to `~/.config/autostart/` to start the tray on login. It runs `ollama-tray` from your `PATH`. On macOS, see [Start at login](#start-at-login).
* While the repository is private, only collaborators can download releases.

### Withdraw a bad release

* Delete the release and its tag with `gh release delete <tag> --cleanup-tag -R stephenbrannen/ollama-tray-python`.
* Fix the problem on `main`, then tag a patch version (for example `v0.2.1`). Do not reuse the deleted tag.

The release workflow runs the tests, refuses a tag that does not match the `pyproject.toml` version, and refuses a tag whose commit is not on `main`.

## Autostart

The entry runs `ollama-tray` from `PATH`, so install the package first (for example with `environment.systemPackages`). Then link the entry from your profile into `~/.config/autostart/`. The profile path is stable across upgrades and garbage collection, unlike a store path.

```sh
mkdir -p ~/.config/autostart
ln -s /run/current-system/sw/share/applications/ollama-tray.desktop ~/.config/autostart/
```

## Development

```sh
nix develop
uv sync
uv run pytest
uv run ruff check
uv run ruff format --check
uv run pyrefly check
uv run pre-commit install
```

The pre-commit hooks run ruff and pyrefly on every commit. A pre-push hook runs pytest with the 90% coverage gate, the same gate CI enforces.

### Project standards

The repository uses Ruff, Pyrefly, pytest and pre-commit, with two accepted deviations from the usual Python standards.

* uv replaces Poetry, and the build backend is `hatchling`, uv's default. The Nix development shell provides Python and uv, so Poetry would add a second tool for no gain.
* Runtime dependencies use a floor plus a major cap (`requests>=2.31,<3`), not `~=`. The Nix package resolves them against nixpkgs, not `uv.lock`, so a tight range would make the Nix build fail whenever nixpkgs lags.

### End-to-end test

```sh
e2e/run.sh
```

This needs Docker with Compose. It starts a real Ollama server and a test container with Xvfb, a session D-Bus and a stub tray host. The test reads the icon, tooltip and menu over D-Bus the way a desktop panel does, clicks `Stop <model>`, and kills the TCP proxy (`socat`) between the tray and Ollama to show the unreachable state. The test container has no Docker socket. The first run downloads `smollm2:135m` into a Docker volume, and later runs reuse it. `uv run pytest` does not collect these tests. The test runs on Linux only: it needs a Linux tray host on D-Bus, and the macOS GitHub runners have no Docker.

Modules in `src/ollama_tray/`:

* `client.py`: the Ollama HTTP client and `resolve_host`. No GUI code.
* `status.py`: maps a client result to a status, tooltip text (with the time until each model unloads) and escaped text. No GUI code.
* `app.py`: the tray icon, menu, worker threads and command line.
