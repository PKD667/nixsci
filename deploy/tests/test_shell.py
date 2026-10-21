"""`nixsci deploy shell`.

Real here: the CLI, the native and ssh backends' command assembly, `env -i`, a pty, bash, and the
shell's exit status. Faked: `nix` (a script that runs the command after `--command`, and
answers `store verify` and `path-info`), `ssh` (a script that records its argv and stdin and runs
the remote command with `sh -c` on this machine), resolving the flake, and staging over ssh.
So these tests show that the command we hand to ssh is a correct, clean-environment command
line; they do not show that a real sshd allocates a pty, forwards resizes, or that a real `nix
shell` puts the closure on PATH.
"""

import base64
import contextlib
import io
import json
import os
import re
import select
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import hashlib

from nixsci.deploy import cli
from nixsci.deploy.backend import Backend
from nixsci.deploy.model import Closure
from nixsci.deploy.shell import shell

STORE = "/nix/store/" + "a" * 32 + "-exp"
ZERO = "sha256-" + base64.b64encode(bytes(32)).decode()
META = {
    "program": STORE + "/bin/run",
    "argv": ["--declared"],
    "env": {"DECLARED": "yes", "PATH_LIKE": "a b"},
    "commands": {"infer": STORE + "/bin/infer"},
}


def closure():
    return Closure(STORE, STORE + "/bin/run", META, {STORE: ZERO}, "src", "x86_64-linux")


class Recording(Backend):
    """Stops where the job's environment is final, and records it."""

    backend_name = "recording"

    def __init__(self):
        self.env, self.spawned, self.removed = None, None, []

    def _admit(self, closure): pass
    def _verify(self, closure): pass
    def stage(self, closure): pass
    def _workdir(self, run_id): return f"/run/x/{run_id}"
    def home(self): return "/home/u"
    def enter(self, closure): return ["nix", "shell", "--offline", closure.path, "--command"]
    def _write_json(self, workdir, name, value): pass
    def exists(self, handle, relpath): return False
    def remove(self, handle): self.removed.append(handle["workdir"])

    def _stage_inputs(self, workdir, records, existing=False):
        return (
            {f"NIX_DEPLOY_INPUT_{n}": f"{workdir}/inputs/{n}" for n, _, _ in records},
            [{"name": n, "path": f"{workdir}/inputs/{n}", "sha256": d} for n, _, d in records],
        )

    def _spawn(self, closure, workdir, run_id, executable, argv, env):
        self.env, self.spawned = dict(env), (executable, tuple(argv))
        return {}


def show(backend, **kwargs):
    out = io.StringIO()
    code = shell(backend, closure(), show=True, out=out, err=io.StringIO(), **kwargs)
    return code, json.loads(out.getvalue())


def envmap(lines):
    return dict(line.split("=", 1) for line in lines)


def inside(text, fakebin):
    """The environment `env` printed inside the fake `nix shell`, minus what that wrapper adds:
    the three variables `sh` exports, and the closure's bin directory on PATH (which the real
    `nix shell` also prepends)."""
    got = envmap(text.splitlines())
    got["PATH"] = got["PATH"].removeprefix(fakebin + ":")
    return {k: v for k, v in got.items() if k not in ("PWD", "SHLVL", "_", "OLDPWD")}


class Show(unittest.TestCase):
    def test_show_is_the_environment_launch_gives_the_program(self):
        for program, env in [("run", None), ("infer", {"SEED": "3", "DECLARED": "over"})]:
            with self.subTest(program=program):
                backend = Recording()
                inputs = {"data": b"abc"}
                backend.launch(closure(), run_id="r1", env=env, inputs=inputs, program=program)
                code, shown = show(backend, run_id="r1", env=env, inputs=inputs, program=program)
                self.assertEqual(code, 0)
                got = envmap(shown["env"])
                self.assertEqual(got.pop("NIX_DEPLOY_PROGRAM"), shown["program"])
                self.assertEqual(got, backend.env)
                self.assertEqual((shown["program"], tuple(shown["argv"])), backend.spawned)
                self.assertEqual(shown["env"], sorted(shown["env"]))

    def test_the_contract_variables_are_there(self):
        _, shown = show(Recording(), run_id="r1", inputs={"data": b"abc"})
        got = envmap(shown["env"])
        self.assertEqual(got["NIX_DEPLOY_WORKDIR"], "/run/x/r1")
        self.assertEqual(got["NIX_DEPLOY_INPUT_data"], "/run/x/r1/inputs/data")
        self.assertEqual(got["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(got["HOME"], "/home/u")
        self.assertEqual(got["DECLARED"], "yes")
        self.assertTrue(got["PATH"].endswith("/usr/bin:/bin"))
        self.assertTrue(got["NIX_DEPLOY_ENTER"].startswith("nix shell --offline"))
        self.assertEqual(shown["argv"], ["--declared"])
        self.assertEqual(shown["target"], "target")

    def test_show_removes_its_workdir_and_keep_leaves_it(self):
        backend = Recording()
        show(backend, run_id="r1")
        self.assertEqual(backend.removed, ["/run/x/r1"])
        backend = Recording()
        show(backend, run_id="r1", keep=True)
        self.assertEqual(backend.removed, [])

    def test_an_existing_workdir_is_never_entered_or_removed(self):
        backend = Recording()
        backend.exists = lambda handle, relpath: True
        with self.assertRaises(FileExistsError):
            show(backend, run_id="r1")
        self.assertEqual(backend.removed, [])


class Delegated(unittest.TestCase):
    """A backend that defines `shell` is handed the call; one that does not gets the built-in path."""

    def test_a_backend_shell_receives_exactly_the_arguments_and_its_status_is_returned(self):
        calls = []

        class Own(Recording):
            def shell(self, closure, **kwargs):
                calls.append((closure, kwargs))
                return 42

            def stage(self, closure): raise AssertionError("built-in path used")

            def _workdir(self, run_id): raise AssertionError("a delegating backend need not have one")

        backend, err = Own(), io.StringIO()
        c = closure()
        code = shell(
            backend, c, target="gw", run_id="r1", program="infer", env={"A": "1"},
            inputs={"data": b"abc"}, cmd=("ls", "-l"), show=True, keep=True,
            out=io.StringIO(), err=err,
        )
        self.assertEqual(code, 42)
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][0], c)
        self.assertEqual(calls[0][1], dict(
            run_id="r1", program="infer", env={"A": "1"}, inputs={"data": b"abc"},
            cmd=("ls", "-l"), show=True, keep=True,
        ))
        self.assertEqual(
            err.getvalue(),
            f"nixsci deploy shell: target=gw closure={STORE}\n",
        )
        self.assertEqual(backend.removed, [])  # the built-in cleanup did not run

    def test_the_status_is_whatever_the_backend_returns(self):
        for status in (0, 3, 130):
            with self.subTest(status=status):
                class Own(Recording):
                    def shell(self, closure, **kwargs): return status

                self.assertEqual(
                    shell(Own(), closure(), run_id="r1", out=io.StringIO(), err=io.StringIO()),
                    status,
                )

    def test_a_backend_without_shell_uses_the_built_in_path(self):
        self.assertFalse(hasattr(Backend, "shell"))
        backend = Recording()
        self.assertFalse(hasattr(backend, "shell"))
        code, shown = show(backend, run_id="r1")
        self.assertEqual(code, 0)
        self.assertEqual(shown["workdir"], "/run/x/r1")
        self.assertEqual(backend.removed, ["/run/x/r1"])


# ---- the CLI, through real processes -------------------------------------------------------

DRIVER = f"""
import os, sys
if os.environ.get("NS_TTY"):
    import fcntl, termios
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
from unittest import mock
from nixsci.deploy import cli
from nixsci.deploy.model import Closure
meta = {META!r}
c = Closure({STORE!r}, {STORE + "/bin/run"!r}, meta, {{{STORE!r}: {ZERO!r}}}, "src", "x86_64-linux")
for target in ("nixsci.deploy.shell.resolve", "nixsci.deploy.cli.resolve"):
    mock.patch(target, lambda *a, **k: c).start()
if os.environ.get("NS_NOSTAGE"):  # staging over ssh needs a real host
    mock.patch("nixsci.deploy.ssh.SSH.stage", lambda self, c: None).start()
sys.exit(cli.main(sys.argv[1:]))
"""

FAKE_NIX = """#!/bin/sh
# Fake nix. Understands `store verify`, `path-info` and `shell --offline PATH --command CMD...`.
while [ $# -gt 0 ]; do
  case "$1" in
    --extra-experimental-features|--store) shift 2;;
    store) exit 0;;
    path-info) printf '%s' '{"@STORE@": {"narHash": "@ZERO@"}}'; exit 0;;
    shell) while [ "$1" != --command ]; do shift; done; shift
           PATH="@BIN@:$PATH"; export PATH
           case "$1" in /nix/store/*) exec env;; esac   # the "program": print the environment
           exec "$@";;
    *) echo "fake nix: $*" >&2; exit 99;;
  esac
done
"""

FAKE_SSH = f"""#!{sys.executable}
import base64, json, os, subprocess, sys
argv = sys.argv[1:]
command = argv[-1]
if "-t" in argv:  # interactive: stdin is the terminal, nothing to record
    stdin = None
elif sys.stdin.isatty():  # a terminal inherited by a non-interactive call: ssh would not wait for it
    stdin = b""
else:
    stdin = sys.stdin.buffer.read()
with open(os.environ["FAKE_SSH_LOG"], "a") as log:
    log.write(json.dumps({{"argv": argv, "stdin": None if stdin is None else
                           base64.b64encode(stdin).decode()}}) + "\\n")
if stdin is None:
    os.execvp("sh", ["sh", "-c", command])
sys.exit(subprocess.run(["sh", "-c", command], input=stdin).returncode)
"""

FAKE_SCP = f"""#!{sys.executable}
import shutil, sys
source, target = sys.argv[-2], sys.argv[-1].split(":", 1)[1]
shutil.copytree(source, target) if "-rp" in sys.argv else shutil.copy2(source, target)
"""


class Cli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="nsh-")
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        cls.bin = os.path.join(cls.tmp, "bin")
        os.mkdir(cls.bin)
        for tool in ("bash", "env", "sh"):
            found = shutil.which(tool)
            if found is None:
                raise unittest.SkipTest(f"no {tool}")
            os.symlink(found, os.path.join(cls.bin, tool))
        cls.nix = cls._script("nix", FAKE_NIX.replace("@BIN@", cls.bin)
                              .replace("@STORE@", STORE).replace("@ZERO@", ZERO))
        cls.ssh = cls._script("ssh", FAKE_SSH)
        cls.scp = cls._script("scp", FAKE_SCP)
        cls.sshlog = os.path.join(cls.tmp, "ssh.log")
        cls.runs = os.path.join(cls.tmp, "runs")
        cls.config = os.path.join(cls.tmp, "targets.json")
        common = {"system": "x86_64-linux", "run_root": cls.runs}
        with open(cls.nix, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        with open(cls.config, "w") as f:
            json.dump({"targets": {
                "box": {"backend": "native", "store": "/nix/store", "rootless": False,
                        "nix_executable": cls.nix, **common},
                "far": {"backend": "ssh", "host": "me@far", "store": os.path.join(cls.tmp, "s"),
                        "rootless": True, "bootstrap": cls.nix, "bootstrap_sha256": digest,
                        "remote_bootstrap": cls.nix, "ssh_command": [cls.ssh], "scp_command": [cls.scp],
                        "jump": "gw@gate", "ssh_options": ["-o", "Foo=bar"], **common},
            }}, f)

    @classmethod
    def _script(cls, name, text):
        path = os.path.join(cls.tmp, name)
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, 0o755)
        return path

    def setUp(self):
        shutil.rmtree(self.runs, ignore_errors=True)
        os.makedirs(self.runs)
        open(self.sshlog, "w").close()
        self.env = {**os.environ, "NS_NOSTAGE": "1", "FAKE_SSH_LOG": self.sshlog}

    def argv(self, target, *rest):
        return [sys.executable, "-c", DRIVER, "--config", self.config, "--target", target, *rest]

    def plain(self, target, *rest):
        return subprocess.run(self.argv(target, *rest), env=self.env, capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, timeout=60)

    def pty(self, target, script, *rest):
        """Run the CLI with a pty as stdin/out/err, type `script`, return (status, output)."""
        try:
            master, slave = os.openpty()
        except OSError as error:
            self.skipTest(f"no pty: {error}")
        process = subprocess.Popen(
            self.argv(target, *rest), stdin=slave, stdout=slave, stderr=slave,
            env={**self.env, "NS_TTY": "1"}, start_new_session=True,
        )
        os.close(slave)
        os.write(master, script.encode())
        out = b""
        try:
            while select.select([master], [], [], 30)[0]:
                try:
                    data = os.read(master, 4096)
                except OSError:  # EIO: every slave end is closed
                    break
                if not data:
                    break
                out += data
            status = process.wait(timeout=30)
        except BaseException:
            process.kill()
            raise
        finally:
            os.close(master)
        text = out.decode(errors="replace").replace("\r", "")
        # Whether bash has printed its prompt before the first typed line's output depends on timing.
        return status, re.sub(r"(?m)^bash-[0-9.]+[#$] ", "", text)

    def runs_left(self):
        return sorted(os.listdir(self.runs))

    # -- native

    def test_a_pty_shell_has_the_job_environment_and_returns_its_exit_status(self):
        status, out = self.pty(
            "box",
            "echo WD=$NIX_DEPLOY_WORKDIR; echo CWD=$PWD; [ -t 0 ] && echo TTY=yes; "
            "echo PROG=$NIX_DEPLOY_PROGRAM; echo DECL=$DECLARED; exit 7\n",
            "shell", "x", "exp", "--run-id", "r1",
        )
        self.assertEqual(status, 7, out)
        workdir = os.path.join(self.runs, "r1")
        for line in (f"WD={workdir}", f"CWD={workdir}", "TTY=yes", f"PROG={STORE}/bin/run",
                     "DECL=yes"):
            self.assertIn("\n" + line + "\n", out)
        self.assertRegex(out, r"nixsci deploy shell: target=box closure=%s workdir=%s\n"
                         % (re.escape(STORE), re.escape(workdir)))
        self.assertEqual(self.runs_left(), [], "the workdir is removed on exit")

    def test_the_shell_sees_exactly_the_shown_environment(self):
        flags = ("--env", "SEED=3", "--env", "DECLARED=over", "--input", "blob=" + self.nix)
        shown = json.loads(self.plain("box", "shell", "x", "exp", "--run-id", "r1", "--show",
                                      *flags).stdout)
        seen = self.plain("box", "shell", "x", "exp", "--run-id", "r1", *flags, "--", "env")
        self.assertEqual(seen.returncode, 0, seen.stderr)
        got = inside(seen.stdout, self.bin)
        self.assertEqual(got, envmap(shown["env"]))
        self.assertEqual(got["SEED"], "3")
        self.assertEqual(got["DECLARED"], "over")
        self.assertEqual(got["NIX_DEPLOY_INPUT_blob"], os.path.join(self.runs, "r1/inputs/blob"))
        self.assertNotIn("FAKE_SSH_LOG", got)  # `env -i`: nothing of the caller's leaks in

    def test_show_equals_what_run_gives_the_program(self):
        handle = os.path.join(self.tmp, "h.json")
        run = self.plain("box", "run", "x", "exp", "r1", "--handle", handle, "--env", "SEED=3")
        self.assertEqual(run.returncode, 0, run.stderr)
        log = os.path.join(self.runs, "r1", "process.log")
        for _ in range(100):
            if os.path.exists(log) and open(log).read().count("NIX_DEPLOY_ENTER="):
                break
            subprocess.run(["sleep", "0.1"])
        ran = inside(open(log).read(), self.bin)
        shutil.rmtree(os.path.join(self.runs, "r1"))
        shown = json.loads(self.plain("box", "shell", "x", "exp", "--run-id", "r1", "--show",
                                      "--env", "SEED=3").stdout)
        got = envmap(shown["env"])
        self.assertEqual(got.pop("NIX_DEPLOY_PROGRAM"), STORE + "/bin/run")
        self.assertEqual(got, ran)

    def test_show_starts_nothing(self):
        done = self.plain("box", "shell", "x", "exp", "--run-id", "r1", "--show", "--", "touch", "x")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["program"], STORE + "/bin/run")
        self.assertEqual(self.runs_left(), [])

    def test_the_command_after_dashes_runs_and_its_status_comes_back(self):
        done = self.plain("box", "shell", "x", "exp", "--", "sh", "-c", "pwd; exit 3")
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertRegex(done.stdout.strip(), re.escape(self.runs) + r"/shell-\d+-\d+$")
        self.assertEqual(self.runs_left(), [])

    def test_keep_leaves_the_workdir_and_without_it_the_workdir_goes(self):
        done = self.plain("box", "shell", "x", "exp", "--run-id", "k", "--keep", "--", "true")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.runs_left(), ["k"])
        self.assertIn("kept workdir", done.stderr)
        again = self.plain("box", "shell", "x", "exp", "--run-id", "k", "--", "true")
        self.assertNotEqual(again.returncode, 0)  # exists: refused, and not removed
        self.assertIn("exists already", again.stderr)
        self.assertEqual(self.runs_left(), ["k"])
        shutil.rmtree(os.path.join(self.runs, "k"))
        done = self.plain("box", "shell", "x", "exp", "--run-id", "k", "--", "false")
        self.assertEqual((done.returncode, self.runs_left()), (1, []))

    def test_a_dirty_flake_is_refused_and_nothing_is_created(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo, exist_ok=True)
        git = ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*git, "init", "-q"], check=True)
        with open(os.path.join(repo, "flake.nix"), "w") as f:
            f.write("{}\n")
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "commit", "-qm", "x"], check=True)
        with open(os.path.join(repo, "flake.nix"), "a") as f:
            f.write("# dirty\n")
        argv = ["--config", self.config, "--target", "box", "shell", repo, "exp", "--", "true"]
        with self.assertRaisesRegex(RuntimeError, "dirty or has untracked files"):
            cli.main(argv)
        self.assertEqual(self.runs_left(), [])

    def test_a_multi_host_lease_is_refused(self):
        with self.assertRaisesRegex(SystemExit, "single host"):
            from nixsci.deploy import shell as module
            module.main(None, ["a", "b"], {}, [object(), object()], None)

    # -- ssh

    def test_over_ssh_the_remote_command_is_clean_and_ssh_gets_a_tty(self):
        status, out = self.pty(
            "far", "echo WD=$NIX_DEPLOY_WORKDIR; echo HOME=$HOME; [ -t 0 ] && echo TTY=yes; "
            "echo LOG=${FAKE_SSH_LOG-unset}; exit 5\n", "shell", "x", "exp", "--run-id", "r1",
        )
        self.assertEqual(status, 5, out)
        workdir = os.path.join(self.runs, "r1")
        for line in (f"WD={workdir}", f"HOME={os.environ['HOME']}", "TTY=yes", "LOG=unset"):
            self.assertIn("\n" + line + "\n", out)
        calls = [json.loads(line) for line in open(self.sshlog)]
        interactive = [c["argv"] for c in calls if "-t" in c["argv"]]
        self.assertEqual(len(interactive), 1)
        argv = interactive[0]
        # ssh options as for run: BatchMode, the jump, the target's own options; then -t HOST CMD
        self.assertEqual(
            argv[:-1],
            ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ProxyJump=gw@gate",
             "-o", "Foo=bar", "-t", "me@far"],
        )
        self.assertTrue(argv[-1].startswith(f"cd {workdir} && exec env -i HOME=\"$HOME\" "))
        self.assertIn(f"NIX_DEPLOY_PROGRAM={STORE}/bin/run", argv[-1])
        self.assertIn(" shell --offline " + STORE + " --command bash --noprofile --norc -i",
                      argv[-1])
        removals = [c for c in calls if f"rm -rf -- {workdir}" in c["argv"][-1]]
        self.assertEqual(len(removals), 1)
        self.assertEqual(self.runs_left(), [])

    def test_over_ssh_inputs_are_staged_and_hash_checked_as_run_does(self):
        done = self.plain("far", "shell", "x", "exp", "--run-id", "r1", "--input", "blob=" + self.nix,
                          "--", "sh", "-c", "cat $NIX_DEPLOY_INPUT_blob | head -1; exit 4")
        self.assertEqual(done.returncode, 4, done.stderr)
        self.assertEqual(done.stdout.strip(), "#!/bin/sh")  # the interactive call has no stdout
        calls = [json.loads(line)["argv"][-1] for line in open(self.sshlog)]
        self.assertTrue(any(c.startswith("sha256sum ") and "/inputs/blob" in c for c in calls))
        self.assertEqual(self.runs_left(), [])


if __name__ == "__main__":
    unittest.main()
