# ollama-tray-python

A Linux system tray icon that shows which models an Ollama server has loaded, and unloads them on demand. Licensed under the [MIT License](LICENSE).

Supported platforms: Linux on x86_64 and aarch64, on X11 or Wayland, with a desktop that shows tray icons. GNOME needs the AppIndicator extension.

## Icon colors

* Green: a model is loaded.
* Grey: the server answers but nothing is loaded. The icon is also grey for the moment before the first check returns.
* Red: the server cannot be reached or does not answer in time. Hover over the icon to see the reason.

## Menu

Right-click the icon.

* `Stop <model>`: unloads that model. One entry per loaded model.
* `Stop all`: unloads every loaded model. Shown when two or more are loaded.
* `Refresh now`: checks the server immediately.
* `Quit`: exits.

If a stop fails, a notification shows the reason.

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

* `nix flake update` refreshes the pinned nixpkgs.
* GNOME needs the AppIndicator extension to show tray icons.

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

The pre-commit hooks run ruff and pyrefly on every commit.

### End-to-end test

```sh
e2e/run.sh
```

This needs Docker with Compose. It starts a real Ollama server and a test container with Xvfb, a session D-Bus and a stub tray host. The test reads the icon, tooltip and menu over D-Bus the way a desktop panel does, clicks `Stop <model>`, and kills the TCP proxy (`socat`) between the tray and Ollama to show the unreachable state. The test container has no Docker socket. The first run downloads `smollm2:135m` into a Docker volume, and later runs reuse it. `uv run pytest` does not collect these tests.

Modules in `src/ollama_tray/`:

* `client.py`: the Ollama HTTP client and `resolve_host`. No GUI code.
* `status.py`: maps a client result to a status, tooltip text and escaped text. No GUI code.
* `app.py`: the tray icon, menu, worker thread and command line.
