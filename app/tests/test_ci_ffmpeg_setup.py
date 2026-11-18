"""CI setup preserves package/suite selection while avoiding stalled mirrors."""
import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess


def installer():
    path=Path(__file__).parents[2]/'bin/install-ci-ffmpeg'
    loader=importlib.machinery.SourceFileLoader('ci_ffmpeg_setup',str(path))
    spec=importlib.util.spec_from_loader(loader.name,loader);module=importlib.util.module_from_spec(spec)
    loader.exec_module(module);return module


def test_ci_setup_uses_https_without_changing_suites_or_third_party_repositories(tmp_path):
    sources=tmp_path/'sources.list.d';sources.mkdir()
    path=sources/'ubuntu.sources'
    path.write_text('Types: deb\nURIs: http://us-east-2.ec2.archive.ubuntu.com/ubuntu\nSuites: resolute resolute-updates\nComponents: main universe\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n')
    classic=tmp_path/'sources.list';classic.write_text('deb http://security.ubuntu.com/ubuntu resolute-security main\n')
    third=sources/'vendor.sources';third.write_text('URIs: https://packages.example.test/vendor\nSuites: stable\n')
    calls=[]
    def run(args,**kwargs):
        calls.append((args,kwargs))
        if args[:2]==['sudo','tee']:Path(args[2]).write_text(kwargs['input'])
    installer().install(sources_dir=tmp_path,run=run)
    assert 'https://archive.ubuntu.com/ubuntu' in path.read_text()
    assert 'Suites: resolute resolute-updates\nComponents: main universe\nSigned-By:' in path.read_text()
    assert classic.read_text()=='deb https://security.ubuntu.com/ubuntu resolute-security main\n'
    assert third.read_text()=='URIs: https://packages.example.test/vendor\nSuites: stable\n'
    apt=[(args,kwargs)for args,kwargs in calls if args[:2]==['sudo','apt-get']]
    assert len(apt)==2
    assert apt[0][0][-1]=='update' and apt[1][0][-3:]==['install','-y','ffmpeg']
    assert all('Acquire::Retries=2' in args and 'Acquire::http::Timeout=30' in args and 'Acquire::https::Timeout=30' in args for args,_ in apt)
    assert all(kwargs['check'] and kwargs['timeout']<=300 for _,kwargs in apt)
    calls.clear();installer().install(sources_dir=tmp_path,run=run)
    assert all(args[:2]!=['sudo','tee']for args,_ in calls),'Already-normalized sources stay untouched'


def test_ci_setup_fails_when_installation_does_not_finish(tmp_path):
    import pytest
    def run(args,**kwargs):raise subprocess.TimeoutExpired(args,kwargs['timeout'])
    with pytest.raises(subprocess.TimeoutExpired):installer().install(sources_dir=tmp_path,run=run)
