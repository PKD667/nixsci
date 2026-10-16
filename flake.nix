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
          pythonImportsCheck = [ "nixsci.cli" "nixsci.deploy" "nixsci.lab" "nixsci.out" ];
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
          out = self.lib.out { inherit pkgs; };
          curve = pkgs.runCommand "demo-curve" { } "mkdir $out; cp ${./out/examples/demo/curve.tsv} $out/curve.tsv";
          values = pkgs.writeText "curve.json" (builtins.toJSON { final = 0.3; epochs = 3; });
          demo = out.project {
            name = "demo";
            style = ./out/examples/demo/demo.sty;
            packages = ps: [ ps.pgfplots ];
            data.curve = if static then out.static ./out/examples/demo/curve.tsv else out.output curve "curve.tsv";
            figures.curve = { src = ./out/examples/demo/curve.tex; data = [ "curve" ]; };
            values.curve = values;
          };
        in
        out.document {
          name = if static then "example-draft" else "example";
          src = ./out/examples/demo/tex;
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
      lib.lab = { pkgs, project }:
        import ./lab/nix/lab.nix {
          inherit pkgs project;
          nixsci = self.packages.${pkgs.stdenv.hostPlatform.system}.nixsci;
          python = self.packages.${pkgs.stdenv.hostPlatform.system}.python;
          rEnv = self.packages.${pkgs.stdenv.hostPlatform.system}.r-env;
        };

      # Documents: `out = nixsci.lib.out { inherit pkgs; }; out.document { ... }` (see out/nix/out.nix).
      lib.out = { pkgs }: import ./out/nix/out.nix { inherit pkgs; crateTool = self.packages.${pkgs.stdenv.hostPlatform.system}.crate-python; };

      # `nix flake init -t github:PKD667/nixsci#paper` (or #slides, #poster).
      templates = {
        paper = { path = ./out/templates/paper; description = "A paper: LaTeX source, PDF and RO-Crate from one flake"; };
        slides = { path = ./out/templates/slides; description = "Beamer slides"; };
        poster = { path = ./out/templates/poster; description = "A tikzposter poster"; };
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
            cp ${./lab}/*.py $site/lab/
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
