"""Validate a cold-backup inventory before stopping customer services."""
import os
import stat

from .runtime_store import RuntimeFault


def inventory(root, max_unpacked, max_archive):
    from .stack import tls_generation_link
    selected=[root/'stack.json',root/'initialized.json',root/'release',root/'secrets',root/'data']
    paths=[];total=0;bound=10240
    for parent in selected:
        if not parent.exists(): raise RuntimeFault('BACKUP_COMPONENT_MISSING')
        for path in [parent]+(sorted(parent.rglob('*')) if parent.is_dir() else []):
            info=path.lstat();link=stat.S_ISLNK(info.st_mode)
            if link:
                target=os.readlink(path)
                if (not tls_generation_link(path.relative_to(root).as_posix(),target)
                        or (path.parent/target).resolve()!=path.parent/target or not (path.parent/target).is_dir()):
                    raise RuntimeFault('BACKUP_SPECIAL_FILE_REJECTED')
                for name in ('certificate.pem','key.pem'):
                    part=(path.parent/target/name).lstat()
                    if not stat.S_ISREG(part.st_mode) or part.st_nlink!=1:
                        raise RuntimeFault('BACKUP_SPECIAL_FILE_REJECTED')
            if (not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) or link)
                    or (stat.S_ISREG(info.st_mode) and info.st_nlink!=1)
                    or info.st_mode & 0o7000 or info.st_uid not in (0,999,1000,10001)
                    or info.st_gid not in (0,999,1000,10001)):
                raise RuntimeFault('BACKUP_SPECIAL_FILE_REJECTED')
            length=info.st_size if stat.S_ISREG(info.st_mode) else 0
            total+=length;paths.append(path)
            # Conservative TAR/PAX and incompressible gzip allowance.
            bound+=((length+511)//512)*512+8192+len(os.fsencode(path))*4
            if total>max_unpacked or len(paths)>100000: raise RuntimeFault('BACKUP_TOO_LARGE')
    bound=bound+bound//100+65536
    if bound>max_archive: raise RuntimeFault('BACKUP_TOO_LARGE')
    return paths,total,bound
