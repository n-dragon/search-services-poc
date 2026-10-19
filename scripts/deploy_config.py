"""Validate deployment inputs before writing a shell-sourceable env file."""
import os
import re


def render(host, domain, commit):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}', host):
        raise ValueError('Invalid SSH_HOST')
    if domain and not re.fullmatch(r'(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', domain):
        raise ValueError('DEMO_DOMAIN must be a bare hostname, without URL, port or path')
    if not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('Expected a full commit SHA')
    return f'DEMO_HOST={host}\nDEMO_DOMAIN={domain}\nDEPLOY_COMMIT={commit}\n'


if __name__ == '__main__':
    print(render(os.environ['SSH_HOST'], os.getenv('DEMO_DOMAIN', ''), os.environ['GITHUB_SHA']), end='')
