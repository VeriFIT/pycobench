{
  description = "pycobench: a small Python framework for running benchmarks";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };

        # Keep in sync with the `dependencies` in pyproject.toml.
        pythonEnv = pkgs.python3.withPackages (
          ps: with ps; [
            pandas
            psutil
            pyyaml
            tabulate
            termcolor
            typing-extensions
          ]
        );

        pycobench = pkgs.stdenvNoCC.mkDerivation {
          pname = "pycobench";
          version = "0.1.0";
          src = ./.;

          nativeBuildInputs = [ pkgs.makeWrapper ];
          dontBuild = true;

          installPhase = ''
            runHook preInstall

            mkdir -p "$out/share/pycobench" "$out/bin"
            cp -r src/. "$out/share/pycobench/"

            for script in pycobench pyco_proc compare_profiles; do
              makeWrapper ${pythonEnv}/bin/python3 "$out/bin/$script" \
                --add-flags "$out/share/pycobench/$script.py" \
                --set PYTHONPATH "$out/share/pycobench"
            done

            makeWrapper "$out/share/pycobench/process_pyco.sh" "$out/bin/process_pyco" \
              --set PYCOBENCH_PYTHON "${pythonEnv}/bin/python3"

            runHook postInstall
          '';

          meta.mainProgram = "pycobench";
        };
      in
      {
        packages.default = pycobench;
        packages.pycobench = pycobench;

        apps.default = {
          type = "app";
          program = "${pycobench}/bin/pycobench";
        };
        apps.pycobench = self.apps.${system}.default;
        apps.pyco_proc = {
          type = "app";
          program = "${pycobench}/bin/pyco_proc";
        };
        apps.compare_profiles = {
          type = "app";
          program = "${pycobench}/bin/compare_profiles";
        };
        apps.process_pyco = {
          type = "app";
          program = "${pycobench}/bin/process_pyco";
        };

        devShells.default = pkgs.mkShell {
          name = "pycobench-dev";
          # `uv sync` manages the actual (locked) dependency set from pyproject.toml/uv.lock;
          # pkgs.python3 is only here as a fallback interpreter for uv to bootstrap against.
          packages = [
            pkgs.uv
            pkgs.python3
          ];
        };
      }
    );
}
