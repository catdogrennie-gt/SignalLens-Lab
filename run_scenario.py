"""VM host launcher: verify actual Docker labels/network before injection."""
import argparse
import json
import subprocess
from pathlib import Path

def validate_container(data):
    labels=data['Config']['Labels']
    required={'sl.owner':'signallens-lab','sl.role':'target','com.docker.compose.project':'signallens-lab'}
    if any(labels.get(key)!=value for key,value in required.items()): raise ValueError('Container labels rejected')
    if data['Name']!='/signallens-lab-target' or data['HostConfig']['NetworkMode']=='host': raise ValueError('Target/network rejected')
    if set(data['NetworkSettings']['Networks'])!={'signallens-lab_lab'}: raise ValueError('Target must have only dedicated Lab network')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('scenario',choices=['lock_wait','sql_load'])
    parser.add_argument('--duration',type=int,default=25)
    args=parser.parse_args()
    if Path.cwd()!=Path('/opt/signallens-lab'): raise ValueError('Run from /opt/signallens-lab')
    data=json.loads(subprocess.check_output(['docker','inspect','signallens-lab-target']))[0]
    validate_container(data)
    import fcntl
    with Path('.runtime/scenario.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        subprocess.run(['docker','compose','exec','-T','probe','python','-m','lab.cli','scenario','--scenario',args.scenario,'--duration',str(args.duration)],check=True,timeout=150)
if __name__=='__main__': main()
