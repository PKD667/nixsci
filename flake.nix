{
  description = "nixsci: a science stack on Nix - deploy, lab, work";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
      # CUDA is unfree (redistributable); only the GPU probe needs it.
      cudaPkgs = system: import nixpkgs { inherit system; config.allowUnfree = true; };

      rootlessBootstrap = pkgs: import ./deploy/nix-bootstrap.nix { inherit pkgs; };

      # The Python library ships the pinned Nix helper used by every compute target.
      library = pkgs: py: {
        nixsci = py.buildPythonPackage {
          pname = "nixsci";
          version = "0.1.0";
          src = ./.;
          pyproject = true;
          build-system = [ py.setuptools ];
          dependencies = [ py.pyarrow ];
          pythonImportsCheck = [ "nixsci.cli" "nixsci.deploy" "nixsci.lab" "nixsci.doc" "nixsci.stats" "nixsci.validate" ];
          # A store path is immutable: bytecode written into one changes its hash and Nix then refuses it.
          makeWrapperArgs = [ "--set" "PYTHONDONTWRITEBYTECODE" "1" ];
          postInstall = ''
            bootstrap=$out/${pkgs.python3.sitePackages}/nixsci/deploy/_bootstrap
            mkdir -p "$bootstrap"
            ln -s "${rootlessBootstrap pkgs}/bin/nix" "$bootstrap/nix"
          '';
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
          (pyfinal: pyprev: library final pyfinal)
        ];
      };

      # Wrap a program so it becomes a nixsci experiment closure.
      #   mkExperiment pkgs { name = "x"; program = "${drv}/bin/x"; metadata = {...}; }
      lib.rootlessBootstrap = { pkgs }: rootlessBootstrap pkgs;
      lib.mkExperiment = pkgs: import ./lab/nix/mk-experiment.nix { inherit pkgs; };
      # The GPU probe: `lib.gpuProbe pkgs` is { probe, driver, experiment } (see gpu/README.md).
      # `pkgs` must allow unfree packages (CUDA); the flake's own output below does that itself.
      lib.gpuProbe = pkgs: import ./gpu { inherit pkgs; mkExperiment = self.lib.mkExperiment pkgs; };
      # Deployment closures implement the deployment.json v2 policy contract.
      lib.mkDeployment = pkgs: import ./deploy/nix/mk-deployment.nix { inherit pkgs; };
      # Experiments and analyses as Nix values. `project` is the calling flake's `self`.
      #   lab = nixsci.lib.lab { inherit pkgs; project = self; };
      lib.fetchRun = pkgs: import ./lab/nix/fetch-run.nix { inherit pkgs; };
      # Verdicts for deterministic checks: `v = lib.validate { inherit pkgs; }; v.check {...}; v.suite {...}`.
      lib.validate = { pkgs }: import ./validate/nix/validate.nix {
        inherit pkgs;
        python = self.packages.${pkgs.stdenv.hostPlatform.system}.python;
      };
      lib.lab = { pkgs, project }:
        import ./lab/nix/lab.nix {
          inherit pkgs project;
          nixsci = self.packages.${pkgs.stdenv.hostPlatform.system}.nixsci;
          python = self.packages.${pkgs.stdenv.hostPlatform.system}.python;
          rEnv = self.packages.${pkgs.stdenv.hostPlatform.system}.r-env;
        };

      # Documents: `doc = nixsci.lib.doc { inherit pkgs; }; doc.document { ... }` (see doc/nix/doc.nix).
      lib.doc = { pkgs }: import ./doc/nix/doc.nix { inherit pkgs; crateTool = self.packages.${pkgs.stdenv.hostPlatform.system}.crate-python; cliTool = self.packages.${pkgs.stdenv.hostPlatform.system}.nixsci; };
      # A flake of declarations by directory convention (doc/nix/workspace.nix):
      #   outputs = inputs: inputs.nixsci.lib.workspace { inherit inputs; root = ./.; };
      lib.workspace = import ./doc/nix/workspace.nix { nixsci = self; };
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
          pp = (pkgs.python3.override { packageOverrides = pyfinal: pyprev: library pkgs pyfinal; }).pkgs; in rec {
          default = nixsci;
          nix-rootless = self.lib.rootlessBootstrap { inherit pkgs; };
          nixsci = pp.nixsci;
          example-paper-publish = (example { static = false; }).publish;
          # The interpreter that has nixsci, for derivations that run its modules.
          python = (pkgs.python3.override { packageOverrides = pyfinal: pyprev: library pkgs pyfinal; }).withPackages (p: [ p.nixsci ]);
          # `from nixsci import lab` with no dependencies: safe to put in any experiment closure.
          lab-py = pkgs.runCommand "nixsci-lab-py" { } ''
            site=$out/${pkgs.python3.sitePackages}/nixsci
            mkdir -p $site
            mkdir $site/lab
            ${pkgs.lib.concatMapStringsSep "\n" (f: "cp ${f} $site/lab/${baseNameOf f}")
              [ ./lab/__init__.py ./lab/schema.py ./lab/run.py ./lab/home.py ./lab/health.py ]}
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
        } // nixpkgs.lib.optionalAttrs (pkgs.stdenv.hostPlatform.system == "x86_64-linux") {
          # The CUDA binary alone (the experiment wraps it): `nix build .#gpu-probe`.
          gpu-probe = (self.lib.gpuProbe (cudaPkgs "x86_64-linux")).probe;
        });

      deployments = forAll (pkgs: nixpkgs.lib.optionalAttrs (pkgs.stdenv.hostPlatform.system == "x86_64-linux") {
        # Which GPU did this run get, and what does it measure (gpu/README.md). NVIDIA, x86_64 only.
        gpu-probe = (self.lib.gpuProbe (cudaPkgs "x86_64-linux")).experiment;
      } // {
        hello = (self.lib.mkDeployment pkgs) {
          name = "hello";
          program = "${pkgs.writeShellScript "hello" ''echo \"hello from $(hostname) workdir=$NIX_DEPLOY_WORKDIR\"''}";
          runtime = [ pkgs.coreutils pkgs.hostname ];
        };
        # Qualifies the patched namespace root before a candidate enters bwrap (deploy/tests/rootless-proof.py).
        namespace-proof = (self.lib.mkDeployment pkgs) {
          name = "namespace-proof";
          program = "${pkgs.writeShellScript "namespace-proof" ''exec ${pkgs.python3Minimal}/bin/python3 ${./deploy/tests/rootless-proof.py} "$@"''}";
          runtime = [ pkgs.python3Minimal pkgs.bubblewrap pkgs.util-linuxMinimal pkgs.coreutils pkgs.tinycc ];
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

      # Verdicts of the examples (validate/examples): `nixsci validate` reads them.
      validate = forAll (pkgs:
        let v = self.lib.validate { inherit pkgs; };
        in v.suite (import ./validate/examples { inherit pkgs v; }));

      # `nix flake check`: the fixtures build, and a draft cannot be published.
      checks = forAll (pkgs:
        let
          rustEmitter = pkgs.rustPlatform.buildRustPackage {
            pname = "nixsci-rust-emitter";
            version = "0.1.0";
            src = ./lab/rust/lab;
            cargoLock.lockFile = ./lab/rust/lab/Cargo.lock;
            cargoBuildFlags = [ "--example" "emit" ];
            doCheck = false;
            installPhase = ''
              mkdir -p $out/bin
              cp target/${pkgs.stdenv.hostPlatform.rust.rustcTarget}/release/examples/emit $out/bin/emit
            '';
          };
          testPython = pkgs.python3.withPackages (p: [ p.pytest p.hypothesis p.pyarrow ]);
        in {
        rust-recorder = pkgs.runCommand "nixsci-rust-recorder-check" {
          nativeBuildInputs = [ testPython ];
        } ''
          cp -R ${./.} nixsci
          chmod -R u+w nixsci
          export PYTHONPATH=$PWD HOME=$TMPDIR/home
          export NIX_LAB_RUST_EMIT=${rustEmitter}/bin/emit
          mkdir -p "$HOME"
          cd nixsci
          pytest -q lab/tests/test_rust.py
          touch $out
        '';

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
          pytest -q deploy/tests lab/tests stats/tests doc/tests --ignore=deploy/tests/test_wrapper.py --ignore=deploy/tests/test_source.py
          touch $out
        '';

        # The suite and the document fixture are the two things a claim in a README rests on, so a
        # build runs both. (Added by the deploy branch: it also covers cli/tests.)
        tests =
          let python = pkgs.python3.withPackages (p: [ p.pytest p.hypothesis p.pyarrow ]);
          in pkgs.runCommand "nixsci-tests"
            {
              nativeBuildInputs = [ python pkgs.git ];
              env.PYTHONDONTWRITEBYTECODE = "1";
            }
            ''
              export HOME=$TMPDIR
              mkdir -p $TMPDIR/work
              cp -r ${self} $TMPDIR/work/nixsci
              cd $TMPDIR/work/nixsci
              chmod -R u+w .
              export PYTHONPATH=$TMPDIR/work
              # test_source.py drives `nix flake metadata` against a real store; the sandbox has a
              # read-only one, so it runs on a machine with a store and not here.
              python3 -m pytest -q deploy/tests lab/tests cli/tests \
                --ignore=deploy/tests/test_source.py > $out 2>&1 || { cat $out; exit 1; }
              printf 'skipped deploy/tests/test_source.py: needs a writable Nix store\n' >> $out
            '';
        # A deterministic check (validate/README.md). `validate.<system>.checks.broken` is left out on purpose.
        validate-sorted = self.validate.${pkgs.stdenv.hostPlatform.system}.checks.sorted;
        example-paper = self.packages.${pkgs.stdenv.hostPlatform.system}.example-paper;
        example-draft = self.packages.${pkgs.stdenv.hostPlatform.system}.example-draft;
        # The zip compiles alone with its README's command (doc.zipCheck), for each example document.
        zip-check-example = (exampleDocument pkgs { static = false; }).zipCheck;
        zip-check-example-draft = (exampleDocument pkgs { static = true; }).zipCheck;
        # Layout laws (doc/nix/laws.nix) over every example document: closure, layout, hygiene.
        layout-laws =
          let
            docOf = name: src: d: { inherit name src; inherit (d) pdf zip zipCheck; };
            examplePaper = exampleDocument pkgs { static = false; };
            exampleDraft = exampleDocument pkgs { static = true; };
          in import ./doc/nix/laws.nix { inherit pkgs; } [
            (docOf "example" ./doc/examples/demo/tex examplePaper)
            (docOf "example-draft" ./doc/examples/demo/tex exampleDraft)
          ];
        publish-refuses-draft =
          let draft = exampleDocument pkgs { static = true; };
          in assert draft.refusals != [ ] && draft.publish.name == "example-draft-publish-refused"; pkgs.runCommand "publish-refuses-draft" { } "touch $out";
      });

      apps = forAll (pkgs: {
        default = { type = "app"; program = "${self.packages.${pkgs.system}.nixsci}/bin/nixsci"; };
        demo = self.lab.${pkgs.system}.experiments.demo.run;
        rootless-lifecycle =
          let
            run = pkgs.writeShellApplication {
              name = "rootless-lifecycle";
              runtimeInputs = [ self.packages.${pkgs.system}.python pkgs.nix pkgs.openssh ];
              text = ''
                exec ${self.packages.${pkgs.system}.python}/bin/python ${./deploy/tests/rootless-lifecycle.py} "$@"
              '';
            };
          in { type = "app"; program = "${run}/bin/rootless-lifecycle"; };
      });
      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ self.packages.${pkgs.system}.python pkgs.python3Packages.pytest pkgs.python3Packages.hypothesis pkgs.python3Packages.pyarrow pkgs.git ];
        };
      });
    };
}
