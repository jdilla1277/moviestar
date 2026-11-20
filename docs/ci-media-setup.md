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
runner image version and setup-helper hash. Older compatible-image caches can
supply candidates, but cannot pin package versions. The verified downloads are
saved immediately after successful setup, before unrelated package tests run.

On an install path the helper refreshes authenticated Ubuntu indexes, validates
restored archives against current package/version/architecture SHA256 records,
then lets apt resolve and install `ffmpeg` normally. Stale, unrecognized, corrupt
and symlink archives are removed; absent metadata discards the affected cache
rather than trusting it. Explicit hashing matters because APT's existing-archive
fast path can accept a same-size file without rehashing it.

Installed FFmpeg remains Ubuntu's maintained distro build, with its normal
package licensing/provenance; this change adds no downloaded third-party binary,
package dependency, vendored executable or wheel/plugin content. Logs record the
runner image, restored/validated archive counts, installed Ubuntu FFmpeg package
version, actual executable versions and generated-media smoke outcome.

## Bounds and failures

The existing limits remain: package-list update <=45 seconds, install <=120
seconds, total helper budget 210 seconds, workflow setup step four minutes and
media-test job 25 minutes. Optional cache restore/save each have a one-minute
limit; cache-service failures fall back to normal verified setup, while an
installer or verification failure still fails the job. Privileged commands use root-owned GNU timeout with
kill escalation. Network/lock waits remain bounded, and setup does not retry
product tests or bypass media coverage.

A cache miss or a package/image update can still require downloads and can fail
when mirrors are unavailable. The cache reduces repeated bulk downloads; it does
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

Run `app/tests/test_ci_ffmpeg_setup.py` for capability, corrupt/stale cache,
install-failure and timeout regressions. Its privileged timeout test only runs
on the real Linux GitHub Actions runner. Qualification needs actual cold and
restored-cache CI logs, including versions and smoke results; mocked tests or a
passing install retry alone do not establish the cache reduced network work.

References: [runner image manifest](https://github.com/actions/runner-images/blob/ubuntu26/20261111.196/images/ubuntu/Ubuntu2604-Readme.md),
[APT command contract](https://manpages.ubuntu.com/manpages/resolute/man8/apt-get.8.html),
and [APT archive acquisition implementation](https://github.com/Debian/apt/blob/main/apt-pkg/acquire-item.cc).
