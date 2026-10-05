"""Read-only operator alerts. Never deliver messages or export private logs."""
import json
import shutil
import time

from .runtime_store import private_file


def alerts(stack):
    from . import workspace, workspace_tls, workspace_acme
    found=[]
    inspected=set()
    def add(code, message, action, project=None, severity='warning'):
        found.append({'code':code,'severity':severity,'project':project,'message':message,'next_step':action})
    free=shutil.disk_usage(stack.root).free
    if free<4*1024**3:
        add('DISK_SPACE_LOW','服务器剩余磁盘空间不足 4 GiB。','扩容或核对可清理文件；不要删除恢复记录。',severity='critical' if free<512*1024**2 else 'warning')
    try:
        for value in workspace.entries(stack):
            project=value['project']
            inspected.add(project)
            try:
                state=workspace.status(stack,project)
                if state.get('recovery_required'):
                    add('WORKSPACE_RECOVERY_REQUIRED','入口替换尚未恢复完成。','先运行 workspace recover，再检查入口。',project,'critical');continue
                if state['status']!='running':
                    add('WORKSPACE_NOT_RUNNING','员工入口当前未完整运行。','核对是否为计划停机，再检查 workspace status。',project)
                tls=workspace_tls.status(stack,project)
                if tls.get('recovery_required'):
                    add('TLS_RECOVERY_REQUIRED','证书替换需要恢复。','先运行 workspace-tls recover。',project,'critical');continue
                days=tls['certificate']['days_remaining']
                if days<30:
                    add('TLS_EXPIRED' if days<=0 else 'TLS_EXPIRING','入口证书已过期。' if days<=0 else '入口证书将在 30 天内到期。','核对 workspace-acme status 并处理续期。',project,'critical' if days<7 else 'warning')
                acme=tls['acme']
                production=acme.get('environments',{}).get('production',{})
                if production.get('requires_context_review'):
                    add('TLS_CONTEXT_REVIEW_REQUIRED','签发配置与当前实例或客户端不一致。','停用续期任务，完成新环境测试签发并核对 reconfigure-plan。',project,'critical')
                if production and production.get('status')!='issued':
                    add('TLS_ISSUANCE_INCOMPLETE','正式证书签发尚未完成。','读取 workspace-acme status 并恢复原签发；不要重复提交。',project,'critical')
                if production.get('status')=='issued' and not acme['schedule'].get('enabled'):
                    add('TLS_TIMER_NOT_CONFIGURED','已有正式证书，但尚未启用自动续期。','核对域名、工作区与证书配置后运行 workspace-acme enable。',project)
                if (acme.get('last_renewal') or {}).get('status')=='failed':
                    add('TLS_RENEWAL_FAILED','最近一次自动续期失败。','读取 workspace-acme status；未知签发先 recover，不重复提交。',project,'critical')
                if acme['schedule'].get('enabled') and not acme['schedule'].get('active'):
                    add('TLS_TIMER_INACTIVE','已配置的续期定时器未运行。','核对实例和证书计划后重新启用定时器。',project,'critical')
            except Exception:
                add('WORKSPACE_INSPECTION_FAILED','无法完成入口或证书检查。','在服务器读取 workspace 和 workspace-acme status，核对恢复状态。',project,'critical')
    except Exception:
        add('WORKSPACE_INVENTORY_FAILED','无法读取工作区清单。','核对工作区配置和文件权限。',severity='critical')
    # Issuance can precede workspace creation; do not hide that unfinished state.
    acmebase=stack.root/'data/acme'
    try:acmeroots=sorted(acmebase.iterdir()) if acmebase.exists() else []
    except Exception:
        acmeroots=[]
        add('TLS_INVENTORY_FAILED','无法读取签发项目清单。','核对签发目录和文件权限。',severity='critical')
    if acmeroots:
        for root in acmeroots:
            if root.name in inspected:continue
            try:
                acme=workspace_acme.status(stack,root.name)
                production=acme.get('environments',{}).get('production',{})
                if production.get('status')=='issued':
                    add('TLS_WORKSPACE_NOT_CONFIGURED','正式证书已签发，员工入口尚未配置。','完成管理员接入后部署工作区，验证公网 HTTPS，再启用续期。',root.name)
                elif production:
                    add('TLS_ISSUANCE_INCOMPLETE','正式证书签发尚未完成。','读取 workspace-acme status 并恢复原签发；不要重复提交。',root.name,'critical')
                if production.get('requires_context_review'):
                    add('TLS_CONTEXT_REVIEW_REQUIRED','签发配置与当前实例或客户端不一致。','停用续期任务，完成新环境测试签发并核对 reconfigure-plan。',root.name,'critical')
            except Exception:
                add('TLS_INSPECTION_FAILED','无法完成签发记录检查。','在服务器核对 workspace-acme status 和私有文件权限。',root.name,'critical')
    base=stack.root/'data/production'
    if base.exists():
        for path in sorted(base.glob('*.json')):
            try:
                private_file(path);value=json.loads(path.read_text())
                if value['status']=='imported_disabled':continue
                if value['status']!='published_restart_verified':
                    add('SCHEDULE_INCOMPLETE','项目调度配置尚未完成。','继续本项目 production-setup。',path.stem);continue
                remaining=value['expires_at']-time.time()
                if remaining<7*86400:
                    add('SCHEDULE_EXPIRED' if remaining<=0 else 'SCHEDULE_EXPIRING','调度授权已到期。' if remaining<=0 else '调度授权将在 7 天内到期。','运行本项目 production-setup 核对并续期。',path.stem,'critical' if remaining<=0 else 'warning')
            except Exception:
                add('SCHEDULE_INSPECTION_FAILED','无法读取项目调度状态。','核对本项目调度记录，勿删除记录后重建。',path.stem,'critical')
    return found


def doctor(stack):
    from .stack import MAX_ARCHIVE, MAX_UNPACKED
    with stack.lock():
        found=alerts(stack)
        try: healthy=stack.status()['infrastructure_ready']
        except Exception: healthy=False
        if not healthy:
            found.insert(0,{'code':'STACK_NOT_READY','severity':'critical','project':None,
                            'message':'基础服务未通过健康检查。','next_step':'先读取 stack status，核对是否为计划停机。'})
        return {'status':'needs_attention' if found else 'no_alerts','alerts':found,
                'backup_capabilities':{'restore':'backup-v2-aes256gcm-hkdf','read_formats':['v1-fernet','v2-streaming-aes256gcm'],
                                       'write_format':'v2-streaming-aes256gcm','max_archive_bytes':MAX_ARCHIVE,'max_unpacked_bytes':MAX_UNPACKED},
                'delivery':'local_operator_only','external_notifications':False,'human_acceptance':'not_run'}
