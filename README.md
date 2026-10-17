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
* `Keep loaded` > `<model>`: keeps that model loaded until you stop it, without changing the server's keep-alive setting. Lists the models that are not already kept loaded, and is hidden when there are none.
* `Stop all`: unloads every loaded model. Shown when two or more are loaded.
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
* `inputs.nixpkgs.follows = "nixpkgs"` makes the package share your system's nixpkgs, so it reuses your Python and PySide6 instead.
* For a one-off run with your own nixpkgs, use `nix run --override-input nixpkgs nixpkgs github:stephenbrannen/ollama-tray-python`.
* A shared nixpkgs must provide Python 3.13 and PySide6 6.x.
* To use the cache for every build on the system, add it to your NixOS configuration. Without this, a system rebuild compiles the package, because Nix ignores the cache settings of a flake used as an input:

  ```nix
  nix.settings = {
    substituters = [ "https://ollama-tray.cachix.org" ];
    trusted-public-keys = [ "ollama-tray.cachix.org-1:VsA+mvlRdugWzqzTzrRhqRpJ25Sc9tbL6z4or9oHc+U=" ];
  };
  ```

* The cache is public, so anyone can download its contents. It holds only `x86_64-linux` builds made against this repository's pinned nixpkgs, so `aarch64-linux` and `inputs.nixpkgs.follows` builds still compile locally.
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

### Project standards

The repository uses Ruff, Pyrefly, pytest and pre-commit, with two accepted deviations from the usual Python standards.

* uv replaces Poetry, and the build backend is `hatchling`, uv's default. The Nix development shell provides Python and uv, so Poetry would add a second tool for no gain.
* Runtime dependencies use a floor plus a major cap (`requests>=2.31,<3`), not `~=`. The Nix package resolves them against nixpkgs, not `uv.lock`, so a tight range would make the Nix build fail whenever nixpkgs lags.

### End-to-end test

```sh
e2e/run.sh
```

This needs Docker with Compose. It starts a real Ollama server and a test container with Xvfb, a session D-Bus and a stub tray host. The test reads the icon, tooltip and menu over D-Bus the way a desktop panel does, clicks `Stop <model>`, and kills the TCP proxy (`socat`) between the tray and Ollama to show the unreachable state. The test container has no Docker socket. The first run downloads `smollm2:135m` into a Docker volume, and later runs reuse it. `uv run pytest` does not collect these tests.

Modules in `src/ollama_tray/`:

* `client.py`: the Ollama HTTP client and `resolve_host`. No GUI code.
* `status.py`: maps a client result to a status, tooltip text (with the time until each model unloads) and escaped text. No GUI code.
* `app.py`: the tray icon, menu, worker thread and command line.
