# ollama-tray-python

A Linux system tray icon that shows which models an Ollama server has loaded, and unloads them on demand.

## Icon colors

* Green: a model is loaded.
* Grey: the server answers but nothing is loaded.
* Red: the server cannot be reached.

## Options

* `--host`: server address. Falls back to `OLLAMA_HOST`, then `http://127.0.0.1:11434`.
* `--interval`: seconds between status checks.
* A host with no port uses 11434 for every scheme, including `https://`.

## Install on NixOS

Try it without installing:

```sh
nix run github:stephenbrannen/ollama-tray-python
```

Or add it to your system configuration:

```nix
# flake.nix
inputs.ollama-tray.url = "github:stephenbrannen/ollama-tray-python";

# configuration.nix
environment.systemPackages = [ inputs.ollama-tray.packages.${pkgs.system}.default ];
```

* While the repository is private, add `access-tokens = github.com=<token>` to `nix.conf`.
* GNOME needs the AppIndicator extension to show tray icons.

## Autostart

Copy or link `share/applications/ollama-tray.desktop` from the package into `~/.config/autostart/`:

```sh
ln -s "$(nix build github:stephenbrannen/ollama-tray-python --no-link --print-out-paths)/share/applications/ollama-tray.desktop" ~/.config/autostart/
```

## Development

```sh
nix develop
uv sync
uv run pytest
```
