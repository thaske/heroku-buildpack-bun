"""Compile-boundary tests; only the external installer/transport/platform are fixtures.

Run: python3 -m unittest discover -s test -p '*_test.py' -v
BUILDPACK_UNDER_TEST may point at a baseline checkout for regression proof.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class CompileCacheTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bun-compile-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = Path(os.environ.get("BUILDPACK_UNDER_TEST", Path(__file__).resolve().parents[1]))
        self.bp = self.root / "buildpack"
        for directory in ("bin", "lib"):
            shutil.copytree(source / directory, self.bp / directory)
        self.fixtures = self.root / "fixtures"
        shutil.copytree(Path(__file__).parent / "fixtures", self.fixtures)
        for fixture in self.fixtures.iterdir():
            fixture.chmod(0o755)
        self.cache = self.root / "cache"
        (self.cache / "bun").mkdir(parents=True)
        (self.cache / "bun" / "keep").write_text("dependency cache")
        for directory in ("home", "tmp", "env", "external-bin"):
            (self.root / directory).mkdir()
        # A PATH executable must never make an invalid cache appear valid.
        external_bun = self.root / "external-bin" / "bun"
        external_bun.write_text("#!/bin/sh\nprintf '99.99.99\\n'\n")
        external_bun.chmod(0o755)
        self.env = os.environ.copy()
        for name in ("BUN_VERSION", "BUN_INSTALL_VERSION", "BUN_INSTALL", "BUN_DIR"):
            self.env.pop(name, None)
        self.env.update(
            HOME=str(self.root / "home"),
            TMPDIR=str(self.root / "tmp"),
            SHELL="/bin/bash",
            PATH=f"{self.fixtures}:{self.root / 'external-bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
            STACK="heroku-24",
            FIXTURE_DIR=str(self.fixtures),
            FIXTURE_PLATFORM="Linux x86_64",
            FIXTURE_CPU_FLAGS="avx2",
            FIXTURE_RELEASE="1.5.0",
            FIXTURE_TRANSPORT_LOG=str(self.root / "transport.log"),
            FIXTURE_INSTALLER_LOG=str(self.root / "installer.log"),
            FIXTURE_RUNTIME_LOG=str(self.root / "runtime.log"),
        )
        self.build_count = 0

    def app(self, version="1.4.2", scripts=False):
        self.build_count += 1
        build = self.root / f"app-{self.build_count}"
        (build / ".heroku" / "bin").mkdir(parents=True)
        (build / ".heroku" / "bin" / "other-buildpack").write_text("do not replace")
        if version is not None:
            (build / ".bun-version").write_text(version + "\n")
        if scripts:
            (build / "package.json").write_text(
                '{"scripts":{"heroku-prebuild":"pre", "build":"build", "heroku-postbuild":"post"}}'
            )
        return build

    def compile(self, build, cache=None, **env):
        result = subprocess.run(
            ["bash", str(self.bp / "bin" / "compile"), str(build),
             str(self.cache) if cache is None else str(cache), str(self.root / "env")],
            env={**self.env, **env}, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((build / ".heroku/bin/other-buildpack").read_text(), "do not replace")
        return result

    def lines(self, name):
        path = self.root / (name + ".log")
        return path.read_text().splitlines() if path.exists() else []

    def version(self, build):
        return subprocess.check_output(
            [str(build / ".heroku/bin/bun"), "--version"], env=self.env, text=True
        ).strip()

    def cache_entry(self):
        entries = list(self.cache.rglob("sha256"))
        self.assertEqual(len(entries), 1)
        return entries[0].parent

    def test_warm_cache_avoids_transport_and_is_relocatable(self):
        cold = self.app()
        self.compile(cold)
        self.assertEqual(self.version(cold), "1.4.2")
        self.assertEqual(self.lines("installer"), ["bun-v1.4.2"])
        warm = self.app("v1.4.2")
        self.compile(warm, FIXTURE_OFFLINE="1")
        self.assertEqual(self.lines("transport"), ["download"])
        self.assertEqual(self.lines("installer"), ["bun-v1.4.2"])
        self.assertEqual((self.cache / "bun/keep").read_text(), "dependency cache")
        self.assertFalse(list(self.cache.rglob("other-buildpack")))
        self.assertFalse((warm / ".heroku/bin/bun").is_symlink())
        self.assertEqual(os.readlink(warm / ".heroku/bin/bunx"), "bun")
        shutil.rmtree(self.cache)
        shutil.rmtree(cold)
        relocated = self.root / "relocated-slug"
        warm.rename(relocated)
        self.assertEqual(self.version(relocated), "1.4.2")
        subprocess.run([str(relocated / ".heroku/bin/bunx"), "fixture-tool"],
                       env=self.env, check=True)
        self.assertEqual(self.lines("runtime"), ["bunx:fixture-tool"])

    def test_release_and_platform_changes_miss_and_replace_previous_entry(self):
        self.compile(self.app())
        changes = [
            ("1.4.3", {}),
            ("1.4.2", {"STACK": "heroku-22"}),
            ("1.4.2", {"FIXTURE_PLATFORM": "Linux aarch64"}),
            ("1.4.2", {"FIXTURE_CPU_FLAGS": "sse2"}),
            ("1.4.2", {"FIXTURE_PLATFORM": "Darwin x86_64", "FIXTURE_CPU_FLAGS": "AVX2"}),
            ("1.4.2", {"FIXTURE_PLATFORM": "Darwin x86_64", "FIXTURE_CPU_FLAGS": "SSE2"}),
            ("1.4.2", {"FIXTURE_PLATFORM": "Darwin arm64"}),
        ]
        for count, (version, environment) in enumerate(changes, start=2):
            with self.subTest(version=version, environment=environment):
                build = self.app(version)
                self.compile(build, **environment)
                self.assertEqual(self.version(build), version)
                self.assertEqual(len(self.lines("installer")), count)
                self.compile(self.app(version), FIXTURE_OFFLINE="1", **environment)
                self.assertEqual(len(self.lines("installer")), count)
                self.cache_entry()  # Publishing replaces, rather than accumulates, runtimes.
        # Rosetta's installer target is the native arm64 artifact, not x64.
        self.compile(self.app(), FIXTURE_OFFLINE="1", FIXTURE_PLATFORM="Darwin x86_64",
                     FIXTURE_ROSETTA="1")

    def test_cache_hit_prunes_leftovers(self):
        self.compile(self.app())
        entry = self.cache_entry()
        root = entry.parent
        stale = [root / "v1.0.0-linux-x64-old", root / ".tmp.abandoned"]
        for directory in stale:
            (directory / "bin").mkdir(parents=True)
            (directory / "bin/bun").write_text("stale")
        installer = self.cache / "bun-installer.sh"
        installer.write_text("stale installer")
        self.compile(self.app(), FIXTURE_OFFLINE="1")
        self.assertEqual(len(self.lines("installer")), 1)
        self.assertEqual(sorted(root.iterdir()), [entry])
        self.assertFalse(installer.exists())
        self.assertEqual((self.cache / "bun/keep").read_text(), "dependency cache")

    def test_invalid_cache_falls_back_and_repairs(self):
        mutations = ("bytes", "metadata", "checksum", "missing-bun", "missing-bunx",
                     "absolute-bunx", "symlink-bun", "not-executable", "wrong-version",
                     "version-command-failure")
        self.compile(self.app())
        for count, mutation in enumerate(mutations, start=2):
            with self.subTest(mutation=mutation):
                entry = self.cache_entry()
                binary = entry / "bin/bun"
                if mutation == "bytes":
                    with binary.open("a") as stream:
                        stream.write("\n# corrupted bytes, same version\n")
                elif mutation == "metadata":
                    (entry / "identity").write_text("incompatible identity")
                elif mutation == "checksum":
                    (entry / "sha256").write_text("0" * 64 + "\n")
                elif mutation == "missing-bun":
                    binary.unlink()
                elif mutation == "missing-bunx":
                    (entry / "bin/bunx").unlink()
                elif mutation == "absolute-bunx":
                    (entry / "bin/bunx").unlink()
                    (entry / "bin/bunx").symlink_to(binary)
                elif mutation == "symlink-bun":
                    outside = self.root / "outside-bun"
                    shutil.copy2(binary, outside)
                    binary.unlink()
                    binary.symlink_to(outside)
                elif mutation == "not-executable":
                    binary.chmod(0o644)
                elif mutation in ("wrong-version", "version-command-failure"):
                    version, status = ("0.0.1", 0) if mutation == "wrong-version" else ("1.4.2", 1)
                    binary.write_text(f"#!/bin/sh\nprintf '{version}\\n'\nexit {status}\n")
                    (entry / "sha256").write_text(hashlib.sha256(binary.read_bytes()).hexdigest() + "\n")
                build = self.app()
                self.compile(build)
                self.assertEqual(self.version(build), "1.4.2")
                self.assertEqual(len(self.lines("installer")), count)
                self.compile(self.app(), FIXTURE_OFFLINE="1")
                self.assertEqual(len(self.lines("installer")), count)

    def test_missing_or_unusable_cache_does_not_fail_install(self):
        unusable = self.root / "cache-is-a-file"
        unusable.write_text("untouched")
        cases = ["", self.root / "missing-cache", unusable]
        for cache in cases:
            with self.subTest(cache=str(cache)):
                build = self.app()
                self.compile(build, cache=cache)
                self.assertEqual(self.version(build), "1.4.2")
        self.assertEqual(unusable.read_text(), "untouched")
        self.assertEqual(len(self.lines("installer")), len(cases))
        # Fail publication while leaving the dependency cache usable.
        owned = self.cache / "heroku-buildpack-bun-runtime"
        owned.write_text("blocked cache publication")
        self.compile(self.app())
        self.assertEqual(owned.read_text(), "blocked cache publication")
        self.assertEqual((self.cache / "bun/keep").read_text(), "dependency cache")

    @unittest.skipIf(os.geteuid() == 0, "root bypasses read-only permissions")
    def test_read_only_cache_does_not_fail_install(self):
        readonly = self.root / "readonly"
        readonly.mkdir()
        readonly.chmod(0o555)
        try:
            build = self.app()
            self.compile(build, cache=readonly)
            self.assertEqual(self.version(build), "1.4.2")
            self.assertEqual(list(readonly.iterdir()), [])
        finally:
            readonly.chmod(0o755)

    def test_default_and_mutable_tags_always_use_installer(self):
        self.compile(self.app())  # An exact cache must not satisfy floating inputs.
        for tag, argument in ((None, "latest"), ("latest", "bun-latest"), ("canary", "bun-canary")):
            for release in ("1.5.0", "1.5.1"):
                with self.subTest(tag=tag, release=release):
                    build = self.app(tag)
                    self.compile(build, FIXTURE_RELEASE=release)
                    self.assertEqual(self.version(build), release)
                    self.assertEqual(self.lines("installer")[-1], argument)
        self.assertEqual(len(self.lines("installer")), 7)
        self.assertEqual(len(self.lines("transport")), 7)
        self.cache_entry()  # Only the initial exact release was published.

    def test_unknown_stack_or_platform_bypasses_cache(self):
        self.compile(self.app())
        for environment in ({"STACK": ""}, {"FIXTURE_PLATFORM": "Unknown x86_64"}):
            for _ in range(2):
                build = self.app()
                self.compile(build, **environment)
                self.assertEqual(self.version(build), "1.4.2")
        self.assertEqual(len(self.lines("installer")), 5)
        self.cache_entry()  # Unknown compatibility never publishes an entry.

    def test_version_file_precedence_over_environment_is_unchanged(self):
        (self.root / "env/BUN_VERSION").write_text("1.4.5")
        files = [(".bun-version", "1.4.2"), ("runtime.bun.txt", "v1.4.3"), ("runtime.txt", "1.4.4")]
        for index, expected in enumerate(("1.4.2", "1.4.3", "1.4.4", "1.4.5")):
            build = self.app(None)
            for name, version in files[index:]:
                (build / name).write_text(version + "\n")
            self.compile(build)
            self.assertEqual(self.version(build), expected)
            self.assertEqual(self.lines("installer")[-1], "bun-v" + expected)

    def test_dependency_flags_hooks_skip_files_and_exports_on_cold_and_warm_builds(self):
        expected = ["bun:install --production --frozen-lockfile", "bun:run heroku-prebuild",
                    "bun:run build", "bun:run heroku-postbuild"]
        cold = self.app(scripts=True)
        self.compile(cold)
        self.assertEqual(self.lines("runtime"), expected)
        self.assertEqual(os.readlink(cold / ".heroku/bin/bunx"), "bun")
        for skipped, remaining in ((["install", "heroku-prebuild"], expected[2:]),
                                   (["build", "heroku-postbuild"], expected[:2])):
            warm = self.app(scripts=True)
            for name in skipped:
                (warm / (".skip-bun-" + name)).touch()
            (self.root / "runtime.log").unlink()
            self.compile(warm, FIXTURE_OFFLINE="1")
            self.assertEqual(self.lines("runtime"), remaining)
        exported = subprocess.check_output(
            ["bash", "-c", 'source "$1"; printf "%s\\n%s\\n" "$PATH" "$BUN_DIR"',
             "bash", str(self.bp / "export")], env=self.env, text=True).splitlines()
        self.assertEqual(exported[0].split(":")[0], str(warm / ".heroku/bin"))
        self.assertEqual(exported[1], str(self.cache / "bun"))
        self.assertTrue(os.access(self.bp / "export", os.X_OK))
        profile = subprocess.check_output(
            ["bash", "-c", 'source "$1"; printf "%s\\n%s\\n" "$PATH" "$BUN_DIR"',
             "bash", str(warm / ".profile.d/bun.sh")],
            env={**self.env, "HOME": str(warm)}, text=True).splitlines()
        self.assertEqual(profile[0].split(":")[0], str(warm / ".heroku/bin"))
        self.assertEqual(profile[1], str(warm / ".bun/install/cache"))
        self.assertEqual(self.lines("transport"), ["download"])


if __name__ == "__main__":
    unittest.main()
