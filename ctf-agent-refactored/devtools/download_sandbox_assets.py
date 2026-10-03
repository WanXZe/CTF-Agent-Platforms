#!/usr/bin/env python3
"""Fetch the pinned public release assets and verify their SHA256 digests."""
import argparse
import hashlib
import os
import subprocess
from pathlib import Path
import json

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proxy',help='Optional curl HTTP/SOCKS proxy; TLS verification remains enabled')
    args=parser.parse_args()
    folder=Path(__file__).resolve().parent/'sandbox-assets'
    entries=json.loads((folder/'manifest.json').read_text())
    for item in entries:
        name=item['name']
        if Path(name).name != name: raise ValueError('Invalid asset filename')
        target=folder/name
        if target.is_file() and digest(target)==item['sha256']:
            print('Verified existing asset:',name,flush=True);continue
        partial=folder/(name+'.download-'+str(os.getpid()))
        command=['curl','--fail','--location','--retry','3','--connect-timeout','20','--output',str(partial)]
        if args.proxy:command+=['--proxy',args.proxy]
        try:
            subprocess.run(command+[item['url']],check=True)
            if digest(partial)!=item['sha256']:raise ValueError('Checksum mismatch: '+name)
            partial.replace(target)
            print('Downloaded and verified:',name,flush=True)
        finally:
            partial.unlink(missing_ok=True)

if __name__=='__main__':main()
