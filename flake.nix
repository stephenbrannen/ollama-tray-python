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
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          pyproject = fromTOML (builtins.readFile ./pyproject.toml);
        in
        {
          default = pkgs.python3Packages.buildPythonApplication {
            pname = pyproject.project.name;
            version = pyproject.project.version;
            pyproject = true;
            src = ./.;

            build-system = [ pkgs.python3Packages.hatchling ];
            dependencies = with pkgs.python3Packages; [
              pyside6
              requests
            ];
            # nixpkgs pins its own PySide6 and requests versions, which may sit outside
            # the `~=` ranges in pyproject.toml.
            pythonRelaxDeps = [
              "pyside6"
              "requests"
            ];

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

            meta.mainProgram = "ollama-tray";
          };
        }
      );

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/ollama-tray";
        };
      });

      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
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
              pkgs.python313
              pkgs.uv
              pkgs.pre-commit
            ];
            # Use the Nix Python so uv does not download its own interpreter.
            UV_PYTHON = "${pkgs.python313}/bin/python3";
            UV_PYTHON_DOWNLOADS = "never";
            LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath qtRuntimeLibs;
          };
        }
      );

      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt);
    };
}
