{
  description = "nixsci: a science stack on Nix - deploy, lab, work";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});

      # The python library and its one executable, `nixsci`.
      library = py: {
        nixsci = py.buildPythonPackage {
          pname = "nixsci";
          version = "0.1.0";
          src = ./.;
          pyproject = true;
          build-system = [ py.setuptools ];
          dependencies = [ py.pyarrow ];
          pythonImportsCheck = [ "nixsci.cli" "nixsci.deploy" "nixsci.lab" "nixsci.doc" "nixsci.stats" ];
          # A store path is immutable: bytecode written into one changes its hash and Nix then refuses it.
          makeWrapperArgs = [ "--set" "PYTHONDONTWRITEBYTECODE" "1" ];
          meta.mainProgram = "nixsci";
        };
      };
      # Self-test fixture: one project with a compiled figure and a value, read by a document.
      # `example-paper` takes its data from a derivation and is clean; `example-draft` takes the
      # same data as a static file, so it is a draft and its `publish` is refused.
      exampleDocument = pkgs: { static }:
        let
          doc = self.lib.doc { inherit pkgs; };
          curve = pkgs.runCommand "demo-curve" { } "mkdir $out; cp ${./doc/examples/demo/curve.tsv} $out/curve.tsv";
          values = pkgs.writeText "curve.json" (builtins.toJSON { final = 0.3; epochs = 3; });
          demo = doc.project {
            name = "demo";
            style = ./doc/examples/demo/demo.sty;
            packages = ps: [ ps.pgfplots ];
            data.curve = if static then doc.static ./doc/examples/demo/curve.tsv else doc.output curve "curve.tsv";
            figures.curve = { src = ./doc/examples/demo/curve.tex; data = [ "curve" ]; };
            values.curve = values;
          };
        in
        doc.document {
          name = if static then "example-draft" else "example";
          src = ./doc/examples/demo/tex;
          projects = [ demo ];
          figures.demo.curve-small = { from = "curve"; width = "6cm"; };
          source = self;
        };
    in {
      # `pkgs.python3.withPackages (p: [ p.nixsci ])` in any consumer that applies this.
      overlays.default = final: prev: {
        pythonPackagesExtensions = prev.pythonPackagesExtensions ++ [
          (pyfinal: pyprev: library pyfinal)
        ];
      };

      # Wrap a program so it becomes a nixsci experiment closure.
      #   mkExperiment pkgs { name = "x"; program = "${drv}/bin/x"; metadata = {...}; }
      lib.mkExperiment = pkgs: import ./lab/nix/mk-experiment.nix { inherit pkgs; };
      # Deployment closures implement the deployment.json v2 policy contract.
      lib.mkDeployment = pkgs: import ./deploy/nix/mk-deployment.nix { inherit pkgs; };
      # Experiments and analyses as Nix values. `project` is the calling flake's `self`.
      #   lab = nixsci.lib.lab { inherit pkgs; project = self; };
      lib.fetchRun = pkgs: import ./lab/nix/fetch-run.nix { inherit pkgs; };
      lib.lab = { pkgs, project }:
        import ./lab/nix/lab.nix {
          inherit pkgs project;
          nixsci = self.packages.${pkgs.stdenv.hostPlatform.system}.nixsci;
          python = self.packages.${pkgs.stdenv.hostPlatform.system}.python;
          rEnv = self.packages.${pkgs.stdenv.hostPlatform.system}.r-env;
        };

      # Documents: `doc = nixsci.lib.doc { inherit pkgs; }; doc.document { ... }` (see doc/nix/doc.nix).
      lib.doc = { pkgs }: import ./doc/nix/doc.nix { inherit pkgs; crateTool = self.packages.${pkgs.stdenv.hostPlatform.system}.crate-python; };
      # Compatibility for flakes pinned before the module was named doc.
      lib.out = self.lib.doc;

      # `nix flake init -t github:PKD667/nixsci#paper` (or #slides, #poster).
      templates = {
        paper = { path = ./doc/templates/paper; description = "A paper: LaTeX source, PDF and RO-Crate from one flake"; };
        slides = { path = ./doc/templates/slides; description = "Beamer slides"; };
        poster = { path = ./doc/templates/poster; description = "A tikzposter poster"; };
      };

      packages = forAll (pkgs:
        let
          example = exampleDocument pkgs;
          pp = (pkgs.python3.override { packageOverrides = pyfinal: pyprev: library pyfinal; }).pkgs; in rec {
          default = nixsci;
          nixsci = pp.nixsci;
          example-paper-publish = (example { static = false; }).publish;
          # The interpreter that has nixsci, for derivations that run its modules.
          python = (pkgs.python3.override { packageOverrides = pyfinal: pyprev: library pyfinal; }).withPackages (p: [ p.nixsci ]);
          # `from nixsci import lab` with no dependencies: safe to put in any experiment closure.
          lab-py = pkgs.runCommand "nixsci-lab-py" { } ''
            site=$out/${pkgs.python3.sitePackages}/nixsci
            mkdir -p $site
            mkdir $site/lab
            ${pkgs.lib.concatMapStringsSep "\n" (f: "cp ${f} $site/lab/${baseNameOf f}")
              [ ./lab/__init__.py ./lab/schema.py ./lab/run.py ./lab/home.py ]}
          '';
          # The Python that writes a document's RO-Crate (rocrate is not in nixpkgs).
          crate-python =
            let
              py = pkgs.python3.pkgs;
              arcp = py.buildPythonPackage {
                pname = "arcp"; version = "0.2.1"; pyproject = true; doCheck = false;
                src = pkgs.fetchPypi { pname = "arcp"; version = "0.2.1"; hash = "sha256-XBeseXLJ74KXnMLK8rOofBrv0/7+mtuKXdcoraV3Fd0="; };
                build-system = [ py.setuptools ];
              };
              rocrate = py.buildPythonPackage {
                pname = "rocrate"; version = "0.15.1"; pyproject = true; doCheck = false;
                src = pkgs.fetchPypi { pname = "rocrate"; version = "0.15.1"; hash = "sha256-T0z27qZlWbqp2bjxCuBuBhG6E+s2RO3Hg7333QBy/O0="; };
                build-system = [ py.setuptools ];
                dependencies = with py; [ requests arcp jinja2 python-dateutil click ];
              };
            in pkgs.python3.withPackages (_: [ rocrate ]);
          example-paper = (example { static = false; }).pdf;
          example-draft = (example { static = true; }).pdf;
          # R package for analysis scripts: use("alias"), runs(), params(), out().
          nixsci-r = pkgs.rPackages.buildRPackage {
            pname = "nixsci";
            version = "0.1.0";
            src = ./lab/r;
            propagatedBuildInputs = with pkgs.rPackages; [ arrow jsonlite ];
          };
          # The pinned R that analyses run with: Rscript plus the analysis packages.
          r-env = pkgs.rWrapper.override {
            packages = with pkgs.rPackages; [ arrow dplyr ggplot2 jsonlite nixsci-r ];
          };
        });

      deployments = forAll (pkgs: {
        hello = (self.lib.mkDeployment pkgs) {
          name = "hello";
          program = "${pkgs.writeShellScript "hello" ''echo \"hello from $(hostname) workdir=$NIX_DEPLOY_WORKDIR\"''}";
          runtime = [ pkgs.coreutils pkgs.hostname ];
        };
        demo = (self.lib.mkExperiment pkgs) {
          name = "demo";
          program = "${pkgs.python3}/bin/python";
          args = [ "${./lab/examples/demo.py}" ];
          env.PYTHONPATH = "${self.packages.${pkgs.system}.lab-py}/${pkgs.python3.sitePackages}";
        };
      });

      # The demo, written as Nix values: `nix run .#demo`, then `nix build .#demo-curves`.
      lab = forAll (pkgs:
        let
          l = self.lib.lab { inherit pkgs; project = self; };
          system = pkgs.stdenv.hostPlatform.system;
        in rec {
          experiments.demo = l.experiment {
            name = "demo";
            closures.${system} = self.deployments.${system}.demo;
            seeds = [ 0 1 ];
            sweep.epsilon = [ 0.1 0.3 ];
            resources = { provider = "local"; hosts = 2; };
            data.loss.columns = { epoch = "int"; value = "float"; split = "str"; };
          };
          analyses.demo-curves = l.analysis {
            name = "demo-curves";
            use.demo = experiments.demo;
            pipelines.curve.script = ./lab/examples/demo.R;
          };
        });

      # An analysis is a derivation: `nix build .#demo-curves`. Not under `packages`: it needs runs
      # in lab.lock, which this repository does not ship, and `nix flake check` evaluates packages.
      legacyPackages = forAll (pkgs: { demo-curves = self.lab.${pkgs.stdenv.hostPlatform.system}.analyses.demo-curves; });

      # `nix flake check`: the fixtures build, and a draft cannot be published.
      checks = forAll (pkgs: {
        recorder = pkgs.runCommand "nixsci-recorder-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
          export PYTHONPATH="${self.packages.${pkgs.stdenv.hostPlatform.system}.lab-py}/${pkgs.python3.sitePackages}"
          export NIX_LAB_DIR=$TMPDIR/records
          python - <<'PYTHON'
          from nixsci import lab
          import os
          lab.record("sample", {"ok": True})
          assert lab.load(os.environ["NIX_LAB_DIR"])[0]["data"] == {"ok": True}
          PYTHON
          touch $out
        '';

        unit = pkgs.runCommand "nixsci-unit-tests" {
          nativeBuildInputs = [ (pkgs.python3.withPackages (p: [ p.pytest p.hypothesis p.pyarrow ]))
            pkgs.coreutils pkgs.bash pkgs.procps pkgs.git ];
        } ''
          cp -R ${./.} nixsci
          chmod -R u+w nixsci
          export PYTHONPATH=$PWD HOME=$TMPDIR/home
          mkdir -p "$HOME"
          cd nixsci
          # Wrapper builds and isolated-store smoke tests run outside the sandbox separately.
          pytest -q deploy/tests lab/tests stats/tests --ignore=deploy/tests/test_wrapper.py --ignore=deploy/tests/test_source.py
          touch $out
        '';

        example-paper = self.packages.${pkgs.stdenv.hostPlatform.system}.example-paper;
        example-draft = self.packages.${pkgs.stdenv.hostPlatform.system}.example-draft;
        publish-refuses-draft =
          let draft = exampleDocument pkgs { static = true; };
          in assert draft.refusals != [ ] && draft.publish.name == "example-draft-publish-refused"; pkgs.runCommand "publish-refuses-draft" { } "touch $out";
      });

      apps = forAll (pkgs: {
        default = { type = "app"; program = "${self.packages.${pkgs.system}.nixsci}/bin/nixsci"; };
        demo = self.lab.${pkgs.system}.experiments.demo.run;
      });
      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ pkgs.python3 pkgs.python3Packages.pytest pkgs.python3Packages.hypothesis pkgs.python3Packages.pyarrow pkgs.git ];
          shellHook = ''export PYTHONPATH="$(dirname "$PWD"):$PYTHONPATH"'';
        };
      });
    };
}
