"""Isolated-git fixture helpers shared by the test modules."""
import contextlib
import os
import subprocess
import tempfile

_FIXED = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
}


def env():
    e = dict(os.environ)
    e["GIT_CONFIG_GLOBAL"] = "/dev/null"
    e.update(_FIXED)
    return e


_env = env


def run(argv, cwd, env=None):
    p = subprocess.run(argv, cwd=cwd, env=env if env is not None else _env(),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return p.stdout.decode("utf-8", "replace")


def make_repo(root):
    os.makedirs(root, exist_ok=True)
    run(["git", "init", "-q", "-b", "main"], root)
    return root


def commit(repo, files, message):
    for rel, data in files.items():
        path = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        run(["git", "add", "--", rel], repo)
    run(["git", "commit", "-q", "-m", message], repo)
    return run(["git", "rev-parse", "HEAD"], repo).strip()


@contextlib.contextmanager
def isolated(tmpdir):
    keys = ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "TMPDIR")
    saved = {k: os.environ.get(k) for k in keys}
    old_tempdir = tempfile.tempdir
    os.environ["GIT_CONFIG_GLOBAL"] = "/dev/null"
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
    os.environ["TMPDIR"] = tmpdir
    tempfile.tempdir = tmpdir  # tempfile caches its directory; TMPDIR alone does not move mkdtemp
    try:
        yield
    finally:
        tempfile.tempdir = old_tempdir
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
