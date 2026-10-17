{
  description = "Development environment for ollama-tray-python";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
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
          pyproject = fromTOML (builtins.readFile ./pyproject.toml);
          pythonPackages = pkgs.${pythonAttr + "Packages"};
        in
        {
          default = pythonPackages.buildPythonApplication {
            pname = pyproject.project.name;
            version = pyproject.project.version;
            pyproject = true;
            src = ./.;

            build-system = [ pythonPackages.hatchling ];
            dependencies = with pythonPackages; [
              pyside6
              requests
              urllib3
            ];

            # nixpkgs ships one combined `pyside6` package and no `pyside6-essentials`, so the
            # runtime dependency check can never find the name pyproject.toml asks for. The
            # `pyside6` dependency above still provides QtCore, QtGui and QtWidgets.
            pythonRemoveDeps = [ "pyside6-essentials" ];

            nativeCheckInputs = with pythonPackages; [
              pytestCheckHook
              responses
            ];
            pythonImportsCheck = [ "ollama_tray" ];
            # Qt needs a platform plugin to import QtWidgets, and the build sandbox has no display.
            preCheck = ''
              export QT_QPA_PLATFORM=offscreen
            '';

            nativeBuildInputs = [ pkgs.qt6.wrapQtAppsHook ];
            # wrapQtAppsHook reads the plugin path from qtbase; qtwayland adds the
            # Wayland platform plugin for Wayland sessions.
            buildInputs = [
              pkgs.qt6.qtbase
              pkgs.qt6.qtwayland
            ];
            # The Python wrapper is built by the Python hooks, so hand the Qt wrapper
            # arguments to it instead of letting wrapQtAppsHook wrap the script twice.
            dontWrapQtApps = true;
            preFixup = ''
              makeWrapperArgs+=("''${qtWrapperArgs[@]}")
            '';

            postInstall = ''
              install -Dm644 ${./ollama-tray.desktop} $out/share/applications/ollama-tray.desktop
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
          # The PySide6 wheels bundle Qt but link against these system libraries,
          # which NixOS does not put on the default loader path.
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
          default = pkgs.mkShell {
            packages = [
              python
              pkgs.uv
              pkgs.pre-commit
            ];
            # Use the Nix Python so uv does not download its own interpreter.
            UV_PYTHON = "${python}/bin/python3";
            UV_PYTHON_DOWNLOADS = "never";
            LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath qtRuntimeLibs;
          };
        }
      );

      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt);
    };
}
