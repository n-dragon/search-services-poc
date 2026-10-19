import pytest
from scripts.deploy_config import render


def test_ip_only_config():
    assert render('51.195.137.119', '', 'a' * 40) == 'DEMO_HOST=51.195.137.119\nDEMO_DOMAIN=\nDEPLOY_COMMIT=' + 'a' * 40 + '\n'


def test_https_config():
    assert 'DEMO_DOMAIN=search.example.org\n' in render('host.example.org', 'search.example.org', 'a' * 40)


@pytest.mark.parametrize('host,domain,sha', [
    ('-oProxyCommand=bad', '', 'a'*40), ('host;id', '', 'a'*40),
    ('host', 'https://example.org', 'a'*40), ('host', 'example.org\nX=bad', 'a'*40),
    ('host', '$(id)', 'a'*40), ('host', '', 'main'),
])
def test_reject_shell_input(host, domain, sha):
    with pytest.raises(ValueError): render(host, domain, sha)
