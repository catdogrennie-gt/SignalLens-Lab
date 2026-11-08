"""Generate task-owned private configuration; never overwrite enrolled identity."""
import json
import os
import secrets
from pathlib import Path

def main():
    base=Path('.runtime')
    for directory in ['private','samples','runs']: (base/directory).mkdir(parents=True,exist_ok=True)
    config=base/'private/target.json'; root=base/'private/root_password'
    if config.exists() or root.exists(): raise SystemExit('Private files exist; refusing to overwrite target identity/secrets.')
    old_umask=os.umask(0o077)
    try:
        root.write_text(secrets.token_urlsafe(32))
        config.write_text(json.dumps(dict(container='signallens-lab-target',owner='signallens-lab',role='target',database='lab_orders',marker='signallens-lab-v1',passwords={user:secrets.token_urlsafe(32) for user in ('monitor','scenario')}),indent=2))
    finally: os.umask(old_umask)
    print('Generated private files under .runtime/private; values omitted.')
if __name__=='__main__': main()
