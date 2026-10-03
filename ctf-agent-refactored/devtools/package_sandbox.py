#!/usr/bin/env python3
"""Package Docker images and a complete build context without application data."""
import argparse
import hashlib
import json
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

EXPECTED_IMAGE='sha256:1f18183141d2f79e91e317597ecebbbcf1952688b14447a51075bfe22b9fd574'
BUILD_FILES=['sandbox.Dockerfile','build_sandbox.sh','sandbox-requirements.txt','ghidra-decompile',
    'DecompileToC.java','sandbox-smoke.py','sandbox-tools.md','sandbox-python-packages.lock',
    'sandbox-pyinstxtractor-packages.lock','sandbox-unipacker-packages.lock','sandbox-apt-packages.lock',
    'download_sandbox_assets.py','package_sandbox.py','SANDBOX_RELEASE.md']

def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def inspect(tag):
    return json.loads(subprocess.check_output(['docker','image','inspect',tag],text=True))[0]

def export(tags,target):
    partial=target.with_name(target.name+'.partial')
    compression='pigz -p 2 -1' if shutil.which('pigz') else 'gzip -1'
    command='docker save '+ ' '.join(shlex.quote(tag) for tag in tags)+' | '+compression+' > '+shlex.quote(str(partial))
    process=subprocess.Popen(['bash','-o','pipefail','-c',command])
    while process.poll() is None:
        size=partial.stat().st_size if partial.exists() else 0
        print(f'Exporting {target.name}: {size/1024**2:.1f} MiB',flush=True);time.sleep(10)
    if process.returncode:raise RuntimeError('Image export failed: '+target.name)
    subprocess.run(['gzip','-t',str(partial)],check=True)
    partial.replace(target)
    print(f'Exported {target.name}: {target.stat().st_size} bytes',flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--verify-import',action='store_true')
    args=parser.parse_args()
    source=Path(__file__).resolve().parent
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    image=inspect('ctf-sandbox:latest');base=inspect('wanxze/nc:latest')
    if image['Id']!=EXPECTED_IMAGE:raise RuntimeError('The active image changed; review before releasing')
    if inspect('ctf-sandbox:re-tools-20261003')['Id']!=EXPECTED_IMAGE:raise RuntimeError('Release tag identity mismatch')
    manifest={'schema':1,'image_id':image['Id'],'image_tags':['ctf-sandbox:latest','ctf-sandbox:re-tools-20261003'],
        'base_image_id':base['Id'],'base_tag':'wanxze/nc:latest','architecture':image['Architecture'],'os':image['Os'],
        'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=source.parents[1],text=True).strip(),
        'source_asset_manifest':json.loads((source/'sandbox-assets/manifest.json').read_text())}
    image_archive=output/'ctf-sandbox-image-linux-amd64.tar.gz'
    export(manifest['image_tags'],image_archive)
    manifest['image_archive']={'name':image_archive.name,'size':image_archive.stat().st_size,'sha256':digest(image_archive)}
    if args.verify_import:
        print('Verifying Docker import of exported image',flush=True)
        subprocess.run(['docker','load','-i',str(image_archive)],check=True)
        if inspect('ctf-sandbox:re-tools-20261003')['Id']!=EXPECTED_IMAGE:raise RuntimeError('Imported image identity mismatch')
    with tempfile.TemporaryDirectory(prefix='sandbox-build-kit-',dir=output) as temporary:
        kit=Path(temporary)/'ctf-sandbox-build-kit';tools=kit/'devtools';tools.mkdir(parents=True)
        for name in BUILD_FILES:shutil.copy2(source/name,tools/name)
        assets=tools/'sandbox-assets';assets.mkdir()
        for name in ['manifest.json','SHA256SUMS','bootstrap-ca.crt']:shutil.copy2(source/'sandbox-assets'/name,assets/name)
        for item in manifest['source_asset_manifest']:
            name=item['name']
            if Path(name).name!=name:raise RuntimeError('Unsafe asset name')
            path=source/'sandbox-assets'/name
            if digest(path)!=item['sha256']:raise RuntimeError('Build asset checksum mismatch: '+name)
            shutil.copy2(path,assets/name)
        export(['wanxze/nc:latest'],kit/'base-image-linux-amd64.tar.gz')
        shutil.copy2(source/'SANDBOX_RELEASE.md',kit/'README.md')
        (kit/'release-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        kit_archive=output/'ctf-sandbox-build-kit.tar.gz'
        print('Packing complete build kit (source, installers, pinned base image)',flush=True)
        with tarfile.open(kit_archive.with_name(kit_archive.name+'.partial'),'w:gz',compresslevel=1) as archive:
            archive.add(kit,arcname='ctf-sandbox-build-kit')
        kit_archive.with_name(kit_archive.name+'.partial').replace(kit_archive)
    shutil.copy2(source/'SANDBOX_RELEASE.md',output/'README.md')
    files=[image_archive,output/'ctf-sandbox-build-kit.tar.gz',output/'README.md']
    # GitHub Release assets must each be smaller than 2 GiB. Split only if needed.
    if image_archive.stat().st_size >= 2*1024**3:
        print('Splitting the image archive into 900 MiB parts',flush=True)
        parts=[]
        with image_archive.open('rb') as stream:
            index=0
            while True:
                first=stream.read(8*1024**2)
                if not first:break
                target=output/(image_archive.name+f'.part{index:03d}');index+=1
                remaining=900*1024**2
                with target.open('wb') as part:
                    part.write(first);remaining-=len(first)
                    while remaining:
                        chunk=stream.read(min(remaining,8*1024**2))
                        if not chunk:break
                        part.write(chunk);remaining-=len(chunk)
                parts.append(target)
        manifest['reassemble_image']=True
        files=parts+files[1:]
    else:manifest['reassemble_image']=False
    manifest['files']=[{'name':file.name,'size':file.stat().st_size,'sha256':digest(file)} for file in files]
    (output/'release-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    files.append(output/'release-manifest.json')
    (output/'SHA256SUMS').write_text(''.join(digest(file)+'  '+file.name+'\n' for file in files),encoding='utf-8')
    subprocess.run(['sha256sum','-c','SHA256SUMS'],cwd=output,check=True)
    print(json.dumps({'status':'packaged','output':str(output),'manifest':manifest},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
