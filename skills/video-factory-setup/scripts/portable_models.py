"""Non-secret portable script profiles. Never infer a provider from a Key.

New providers require explicit opt-in and their own live acceptance. Saved
projects keep their exact selection; a resumed setup cannot repurpose a Key.
"""
import json
import re
from pathlib import Path
from portable_n8n import Fault, identifier, locked, private_write

SCRIPTS = {
    'deepseek': ('DeepSeek', 'https://api.deepseek.com', 'deepseek-flash'),
    'openai': ('OpenAI', 'https://api.openai.com/v1', 'gpt-4.1-mini'),
    'gemini': ('Gemini', 'https://generativelanguage.googleapis.com/v1beta/openai', 'gemini-3.8-flash'),
    'minimax-cn': ('MiniMax China', 'https://api.minimax.cn/v1', 'MiniMax-M2.7'),
    'minimax-global': ('MiniMax Global', 'https://api.minimax.io/v1', 'MiniMax-M2.7'),
    'qwen-cn': ('Qwen China', 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'qwen-plus'),
    'doubao-cn': ('Doubao China', 'https://ark.cn-beijing.volces.com/api/v3', 'doubao-seed-2-1-lite-260915'),
}


def selection(provider='deepseek', model=None):
    if provider not in SCRIPTS:
        raise Fault('SCRIPT_PROVIDER_UNSUPPORTED')
    model = SCRIPTS[provider][2] if model is None else model
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', model):
        raise Fault('SCRIPT_MODEL_INVALID')
    return {'provider': provider, 'model': model}


def checked(value):
    if not isinstance(value, dict) or set(value) != {'provider', 'model'}:
        raise Fault('SCRIPT_SELECTION_INVALID')
    return selection(value['provider'], value['model'])


def profile(value):
    value = checked(value)
    provider = value['provider']
    label, endpoint, _ = SCRIPTS[provider]
    result = {'provider': label, 'model': value['model'], 'endpoint': endpoint + '/chat/completions',
              'max_tokens': 2048, 'stream': False}
    if provider in ('deepseek', 'doubao-cn'):
        result['thinking'] = {'type': 'disabled'}
    if provider == 'qwen-cn':
        result['enable_thinking'] = False
    if provider.startswith('minimax-'):
        result.pop('max_tokens')
        result.update(max_completion_tokens=4096, reasoning_split=True)
    return result


def load(root, project, origin):
    path = Path(root) / ('models-' + identifier(project) + '.json')
    if not path.exists():
        return selection()
    saved = json.loads(path.read_text(encoding='utf-8'))
    if saved.get('origin') != origin or saved.get('project') != project:
        raise Fault('MODEL_CONFIGURATION_SCOPE_CHANGED')
    return checked(saved['script'])


def save(root, project, origin, value):
    value = checked(value)
    with locked(root) as root:
        path = root / ('models-' + identifier(project) + '.json')
        if path.exists():
            if load(root, project, origin) != value:
                raise Fault('MODEL_CHANGE_REQUIRES_NEW_PROJECT_AND_FEE_ACCEPTANCE')
        else:
            credentials = root / ('credentials-' + project + '.json')
            if credentials.exists():
                saved = json.loads(credentials.read_text(encoding='utf-8'))
                if saved.get('origin') != origin or saved.get('project') != project:
                    raise Fault('PROJECT_CREDENTIAL_SCOPE_CHANGED')
                # Legacy script credentials are specifically DeepSeek credentials.
                if 'script' in saved.get('roles', {}) and value != selection():
                    raise Fault('EXISTING_SCRIPT_KEY_CANNOT_CHANGE_PROVIDER')
            private_write(path, {'origin': origin, 'project': project, 'script': value})
    return value


def options(root, project, origin):
    """Only non-default profiles alter historical workflow templates."""
    from portable_video_models import load as load_video, selection as video_default
    script=load(root,project,origin);video=load_video(root,project,origin)
    result={}
    if script!=selection():result['script_selection']=script
    if video!=video_default():result['video_selection']=video
    from portable_images import load as load_image
    image=load_image(root,project,origin)
    if image is not None: result['image_selection']=image
    return result
