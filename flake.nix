{
  description = "Development environment for ollama-tray-python";

  # Nix asks once whether to trust these settings, then downloads the
  # prebuilt package instead of compiling it.
  nixConfig = {
    extra-substituters = [ "https://ollama-tray.cachix.org" ];
    extra-trusted-public-keys = [
      "ollama-tray.cachix.org-1:VsA+mvlRdugWzqzTzrRhqRpJ25Sc9tbL6z4or9oHc+U="
    ];
  };

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        # Intel Macs are left out: nixpkgs 26.11 dropped x86_64-darwin, and the release
        # wheel still runs there.
        "aarch64-darwin"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      # The package and the dev shell both read this name, so a flake.lock update that moves
      # nixpkgs' default Python cannot break the requires-python check.
      pythonAttr = "python313";
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          inherit (pkgs.stdenv.hostPlatform) isDarwin isLinux;
          pyproject = fromTOML (builtins.readFile ./pyproject.toml);
          pythonPackages = pkgs.${pythonAttr + "Packages"};
          # nixpkgs' pyside6 binds every Qt module, which makes the closure about 2.2 GiB.
          # QtWebEngine alone is 469 MiB. The app uses only QtCore, QtGui and QtWidgets, and
          # the tests add QtTest. So this build binds those four and links only qtbase.
          pyside6Trimmed = pythonPackages.pyside6.overrideAttrs (old: {
            # These are the required inputs of nixpkgs' pyside6 expression. If a nixpkgs
            # update changes that list, copy the new required inputs here.
            buildInputs = [
              pkgs.qt6.qtbase
              pythonPackages.ninja
              pythonPackages.packaging
              pythonPackages.setuptools
            ]
            # On macOS, nixpkgs builds Qt against a pinned Apple SDK and minimum macOS version.
            # The pyside6 expression adds the same inputs there, so the bindings match Qt.
            # Its joined Qt tree is not needed here: qtbase alone holds every framework we bind.
            ++ pkgs.lib.optionals isDarwin pkgs.qt6.darwinVersionInputs;
            # `MODULES` is a CMake option of the pyside6 source, not of nixpkgs.
            cmakeFlags = old.cmakeFlags ++ [ "-DMODULES=Core;Gui;Widgets;Test" ];
            # The generated bindings are large C++ files. With 18 parallel compile jobs, the
            # build ran out of memory in 8 GiB. Four jobs fit there and match a GitHub runner.
            # The ninja hook reads NIX_BUILD_CORES after preBuild runs.
            preBuild = (old.preBuild or "") + ''
              if [ "$NIX_BUILD_CORES" -gt 4 ]; then NIX_BUILD_CORES=4; fi
            '';
          });
        in
        {
          default = pythonPackages.buildPythonApplication {
            pname = pyproject.project.name;
            version = pyproject.project.version;
            pyproject = true;
            src = ./.;

            build-system = [ pythonPackages.hatchling ];
            dependencies = [
              pyside6Trimmed
              pythonPackages.requests
              pythonPackages.urllib3
            ];

            # nixpkgs ships one combined `pyside6` package and no `pyside6-essentials`, so the
            # runtime dependency check can never find the name pyproject.toml asks for. The
            # `pyside6Trimmed` dependency above still provides QtCore, QtGui and QtWidgets.
            pythonRemoveDeps = [ "pyside6-essentials" ];

            nativeCheckInputs = with pythonPackages; [
              pytestCheckHook
              responses
            ];
            pythonImportsCheck = [ "ollama_tray" ];
            # Qt needs a platform plugin to import QtWidgets, and the build sandbox has no display.
            # qtbase builds the offscreen plugin on Linux and macOS alike.
            preCheck = ''
              export QT_QPA_PLATFORM=offscreen
            '';

            nativeBuildInputs = [ pkgs.qt6.wrapQtAppsHook ];
            # wrapQtAppsHook reads the plugin path from qtbase. Since Qt 6.11, qtbase ships the
            # Wayland platform plugin. The 6.11 qtwayland package holds compositor libraries and
            # window decorations, and a tray icon with no windows uses neither.
            # On macOS the same plugin path holds the `cocoa` platform plugin, which draws the
            # menu bar icon.
            buildInputs = [ pkgs.qt6.qtbase ];
            # The Python wrapper is built by the Python hooks, so hand the Qt wrapper
            # arguments to it instead of letting wrapQtAppsHook wrap the script twice.
            dontWrapQtApps = true;
            preFixup = ''
              makeWrapperArgs+=("''${qtWrapperArgs[@]}")
            '';

            # Desktop entries are a freedesktop.org standard. macOS ignores them and starts
            # programs at login through Login Items or launchd instead.
            postInstall =
              pkgs.lib.optionalString isLinux ''
                install -Dm644 ${./ollama-tray.desktop} $out/share/applications/ollama-tray.desktop
              ''
              + ''
                # Nix builds have no release tag, so `--version` reads the commit from this file.
                printf '%s\n' '${self.shortRev or self.dirtyShortRev or "dirty"}' \
                  > $out/${pythonPackages.python.sitePackages}/ollama_tray/_build_version
              '';

            meta = {
              description = pyproject.project.description;
              license = pkgs.lib.licenses.mit;
              mainProgram = "ollama-tray";
              platforms = systems;
            };
          };
        }
      );

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/ollama-tray";
        };
      });

      checks = forAllSystems (system: {
        package = self.packages.${system}.default;
      });

      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.${pythonAttr};
          # The Linux PySide6 wheels bundle Qt but link against these system libraries,
          # which NixOS does not put on the default loader path. The macOS wheels link only
          # system frameworks, so the shell sets no library path there.
          qtRuntimeLibs = with pkgs; [
            libGL
            fontconfig
            freetype
            dbus
            libxkbcommon
            wayland
            glib
            zstd
            libx11
            libxcb
            libxext
            libxrender
            libxi
            libxcursor
            libxrandr
            libxfixes
            libxcb-cursor
            libxcb-image
            libxcb-keysyms
            libxcb-render-util
            libxcb-wm
          ];
        in
        {
          default = pkgs.mkShell (
            {
              packages = [
                python
                pkgs.uv
                pkgs.pre-commit
              ];
              # Use the Nix Python so uv does not download its own interpreter.
              UV_PYTHON = "${python}/bin/python3";
              UV_PYTHON_DOWNLOADS = "never";
            }
            // pkgs.lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
              LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath qtRuntimeLibs;
            }
          );
        }
      );

      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt);
    };
}
