#!/usr/bin/env python3
"""Restore existing persistent shadow/Lv homes after a Pod rebuild (root only)."""
import grp
import os
import pathlib
import pwd
import subprocess

roles = {'qmzvskoi8lpqmq': 'cpu', 'z07aj01v9vefp3': 'gpu'}
role = roles.get(os.environ.get('RUNPOD_POD_ID'))
if os.geteuid() != 0 or role is None:
    raise SystemExit('Run as root on one of the configured Pods.')
groups = [('workspace-editors', 21000), ('shadow-private', 21001), ('Lv-private', 21003)]
for name, gid in groups:
    if any(g.gr_gid == gid and g.gr_name != name for g in grp.getgrall()):
        raise SystemExit('Group ID conflict: ' + str(gid))
    try:
        group = grp.getgrnam(name)
    except KeyError:
        subprocess.run(['groupadd', '-g', str(gid), name], check=True)
    else:
        if group.gr_gid != gid:
            raise SystemExit('Unexpected group ID: ' + name)
for name, uid, gid, private in [('shadow', 21000, 21001, 'shadow-private'), ('Lv', 21002, 21003, 'Lv-private')]:
    home = '/workspace/.users/' + name + '-' + role
    if not pathlib.Path(home).is_dir() or pathlib.Path(home).stat().st_uid != uid:
        raise SystemExit('Missing or unexpected persistent home: ' + home)
    if any(u.pw_uid == uid and u.pw_name != name for u in pwd.getpwall()):
        raise SystemExit('User ID conflict: ' + str(uid))
    try:
        user = pwd.getpwnam(name)
    except KeyError:
        subprocess.run(['useradd', '-M', '-u', str(uid), '-g', private, '-G', 'workspace-editors',
                        '-d', home, '-s', '/bin/bash', name], check=True)
    else:
        if (user.pw_uid, user.pw_gid, user.pw_dir) != (uid, gid, home):
            raise SystemExit('Existing user differs: ' + name)
        subprocess.run(['usermod', '-aG', 'workspace-editors', name], check=True)
    print('Restored', name, 'on', role)
