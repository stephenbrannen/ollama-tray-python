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
