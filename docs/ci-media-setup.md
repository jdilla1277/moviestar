# FFmpeg setup for package CI

The media-test jobs use `ubuntu-26.04`, Python 3.12 and Ubuntu's FFmpeg package.
`bin/setup-ci-ffmpeg` first tries tools already on PATH. It requires matching
FFmpeg/ffprobe major versions >=6, the named filters/encoders in the helper,
and a generated H.264/AAC render executing drawtext and libass. Every job checks
these capabilities; a cache hit is never acceptance by itself.

## Download cache and provenance

The Ubuntu 26.04 image used during the 0.8.4 release had no FFmpeg on PATH.
Its package-list update succeeded, but downloading the dependency set from
`azure.archive.ubuntu.com` repeatedly exceeded the 120-second installation
limit ([#255](https://github.com/jdilla1277/moviestar/issues/255)). A passing retry
did not remove the recurring bulk-download dependency.

CI now restores only completed `.deb` downloads into a dedicated directory under
`RUNNER_TEMP`; it never caches installed libraries, dpkg state, package indexes
or partially downloaded archives. Keys include Ubuntu release, runner architecture,
runner image version and setup-helper hash. Restore uses a unique lookup key
and prefers the latest content generation, so an old exact key cannot shadow a
repaired cache. Older compatible-image caches can supply candidates, but cannot
pin package versions.

Before and after setup, optional fingerprint steps hash archive names and bytes,
ignoring path and mtime. Changed contents or a legacy namespace cause a new save
key containing the verified digest plus run ID/attempt. The generation suffix
also heals a corrupt remote entry whose key still names the original good digest:
repairing back to those same bytes must not reuse the immutable damaged key.
Unchanged contents already under the current content prefix are reused without
saving a duplicate each run. Verified downloads are saved immediately after
successful setup, before unrelated package tests run.

On an install path the helper refreshes authenticated Ubuntu indexes, validates
restored archives against current package/version/architecture SHA256 records
(including Ubuntu 26.04 `Architecture-Variant` filenames such as `amd64v3`),
then lets apt resolve and install `ffmpeg` normally. Metadata lookup uses native
package names: a CPU build variant is not a Debian architecture qualifier. Stale, unrecognized, corrupt
and symlink archives are removed; even the initial byte-count diagnostic uses
`lstat()` so a dangling target cannot prevent validation or installation. Absent
metadata discards the affected cache
rather than trusting it. Explicit hashing matters because APT's existing-archive
fast path can accept a same-size file without rehashing it.

Installed FFmpeg remains Ubuntu's maintained distro build, with its normal
package licensing/provenance; this change adds no alternate binary provider,
package dependency, vendored executable or wheel/plugin content. Logs record the
runner image, restored/validated archive counts, installed Ubuntu FFmpeg package
version, actual executable versions and generated-media smoke outcome.

## Bounds and failures

The existing limits remain: package-list update <=45 seconds, install <=120
seconds, total helper budget 210 seconds, workflow setup step four minutes and
media-test job 25 minutes. Optional cache restore/save and fingerprint steps each
have a one-minute workflow limit; the fingerprint helper has its own 20-second
budget. Cache-service/fingerprint failures do not bypass normal verified setup
or fail otherwise healthy package tests, while an installer or verification
failure still fails the job. Privileged commands use root-owned GNU timeout with
kill escalation. Network/lock waits remain bounded, and setup does not retry
product tests or bypass media coverage.

A cache miss or a package/image update can still require downloads and can fail
when mirrors are unavailable. GitHub scopes PR-created caches to that PR, so
the first run on main must seed its own cache; later PRs can reuse main caches.
The cache reduces repeated bulk downloads; it does
not claim to make cold network installation infallible. A failed update/install,
missing capability or bad smoke output stops the job before package tests.

To verify existing local tools without installation:

```bash
python bin/setup-ci-ffmpeg --check-only
```

To use downloaded packages on an authorized Ubuntu runner:

```bash
python bin/setup-ci-ffmpeg --apt-cache "$RUNNER_TEMP/moviestar-apt"
```

The CI-only `--archive-fingerprint DIRECTORY` mode prints `digest=...` for
completed regular archives without invoking tools or installing packages. Empty
caches emit an empty digest; invalid file types emit no save key and fail only
the optional fingerprint step.

Run `app/tests/test_ci_ffmpeg_setup.py` for capability, corrupt/stale cache,
dangling links through the complete setup path, immutable-cache repair/update
followed by a zero-download restore, install-failure and timeout regressions. Its privileged timeout test only runs
on the real Linux GitHub Actions runner. Qualification needs actual cold and
restored-cache CI logs, including versions and smoke results; mocked tests or a
passing install retry alone do not establish the cache reduced network work.

References: [runner image manifest](https://github.com/actions/runner-images/blob/ubuntu26/20261111.196/images/ubuntu/Ubuntu2604-Readme.md),
[APT command contract](https://manpages.ubuntu.com/manpages/resolute/man8/apt-get.8.html),
and [APT archive acquisition implementation](https://github.com/Debian/apt/blob/main/apt-pkg/acquire-item.cc),
[GitHub cache access scope](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#restrictions-for-accessing-a-cache),
and [immutable cache refresh](https://github.com/actions/cache/blob/main/tips-and-workarounds.md#update-a-cache).
