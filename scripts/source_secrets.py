"""Write optional API credentials for transfer; never include them in source archives."""
import os
from pathlib import Path
import shlex


def render(environment):
    rows=[]
    for name in ('MP_API_KEY','OPENALEX_API_KEY'):
        value=environment.get(name,'')
        if len(value)>512 or any(ord(c)<32 or ord(c)==127 for c in value):
            raise ValueError('Invalid credential format for '+name)
        rows.append(name+'='+shlex.quote(value))
    return '\n'.join(rows)+'\n'


if __name__=='__main__':
    path=Path('.source-secrets.env')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as stream: stream.write(render(os.environ))
    path.chmod(0o600)
