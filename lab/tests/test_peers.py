import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from nixsci.lab import peers, publish
from nixsci.lab.compact import compact_runs


def test_lock_v2_roundtrip_and_v1_compatibility(tmp_path):
    old = {"demo": [{"name": "r", "path": "/nix/store/path", "sha256": "sha256-x"}]}
    (tmp_path / "lab.lock").write_text(json.dumps(old))
    assert publish.read_lock(tmp_path) == old
    publish._write_lock(tmp_path, old)
    assert json.loads((tmp_path / "lab.lock").read_text())["format"] == "2.0"
    assert publish.read_lock(tmp_path) == old


def test_unknown_lock_major_rejected(tmp_path):
    (tmp_path / "lab.lock").write_text('{"format":"3.0", "runs":{}}')
    with pytest.raises(ValueError, match="unsupported"):
        publish.read_lock(tmp_path)


def test_facts_checked_against_manifest(tmp_path):
    m = {"format": "2.0", "app": "e", "run": "r", "state": "ok", "seed": 0, "closure": "/old"}
    run = tmp_path / "r"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps(m))
    expected = publish.facts(m)
    expected["closure"] = "/new"  # editing the lock cannot relabel which program produced the data
    with pytest.raises(ValueError, match="facts differ"):
        compact_runs(tmp_path / "out", [run], {"r": expected})


def test_peer_config_is_machine_local(tmp_path, monkeypatch):
    config = tmp_path / "peers.json"
    config.write_text('{"peers":["file:///a","ssh-ng://b","file:///a"]}')
    monkeypatch.setenv("NIXSCI_PEERS_FILE", str(config))
    assert peers.peers() == ["file:///a", "ssh-ng://b"]


def test_pull_tries_each_peer_then_verifies_and_roots(tmp_path):
    entry = {"name": "r", "path": "/nix/store/absent-test-r", "sha256": "sha256-r"}
    calls = []

    def run(*args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1 if "file:///bad" in args else 0, "", "not here")

    with patch.object(peers, "peers", return_value=["file:///bad", "file:///good"]), \
         patch.object(publish, "read_lock", return_value={"demo": [entry]}), \
         patch.object(peers, "_run", side_effect=run), \
         patch.object(peers, "canonical_path", return_value=entry["path"]), \
         patch.object(peers, "verify") as verify, patch.object(peers, "root") as root:
        assert peers.transfer(tmp_path, "pull", log=lambda _: None) == 0
        assert len(calls) == 2
        verify.assert_called_once_with(entry)
        root.assert_called_once_with(entry["path"])
        assert all("--no-check-sigs" not in c for c in calls)


def test_push_roots_ssh_peer_and_local_copy(tmp_path):
    entry = {"name": "r", "path": "/nix/store/test-r", "sha256": "sha256-r"}
    with patch.object(publish, "read_lock", return_value={"demo": [entry]}), \
         patch.object(peers, "_run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
         patch.object(peers, "verify"), patch.object(peers, "canonical_path", return_value=entry["path"]), \
         patch.object(peers, "root") as local, \
         patch.object(peers, "remote_root") as remote:
        assert peers.transfer(tmp_path, "push", "ssh-ng://node", log=lambda _: None) == 0
        local.assert_called_once_with(entry["path"])
        remote.assert_called_once_with("ssh-ng://node", entry["path"])


def test_remote_root_uses_given_port_and_quotes_path():
    with patch.object(peers, "_run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
        peers.remote_root("ssh-ng://user@node:2222", "/nix/store/abc-r")
        assert run.call_args.args[:4] == ("ssh", "-p", "2222", "user@node")
        assert "--add-root" in run.call_args.args[-1]


@pytest.mark.parametrize('version', ['1.0', '1.99', '2.0', '2.42', None])
def test_old_and_additive_run_formats_are_readable(tmp_path, version):
    from nixsci.lab.run import read_manifest
    data = {"app": "demo", "run": "r"}
    if version is not None:
        data["format"] = version
    p = tmp_path / 'manifest.json'
    p.write_text(json.dumps(data))
    assert read_manifest(p) == data


@pytest.mark.parametrize('version', ['3.0', '2', '2.bad', {}, 2])
def test_future_and_malformed_run_formats_fail(tmp_path, version):
    from nixsci.lab.run import read_manifest
    p = tmp_path / 'manifest.json'
    p.write_text(json.dumps({"format": version}))
    with pytest.raises(ValueError, match='unsupported'):
        read_manifest(p)


def test_transfers_reject_noncanonical_path():
    entry = {"name": "r", "sha256": "sha256-r", "path": "/nix/store/other-r"}
    with patch.object(peers, "_run", return_value=subprocess.CompletedProcess([], 0, "/nix/store/canonical-r\n", "")):
        with pytest.raises(ValueError, match="path differs"):
            peers.canonical_path(entry)


def test_facts_cannot_be_bypassed_by_an_unmatched_manifest_name(tmp_path):
    run = tmp_path / "locked"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({"run": "some-other-run", "state": "ok"}))
    with pytest.raises(ValueError, match="no matching locked identity"):
        compact_runs(tmp_path / "out", [run], {"locked": None})
