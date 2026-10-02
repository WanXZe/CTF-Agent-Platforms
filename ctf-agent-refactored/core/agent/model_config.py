"""Atomic model connections and defaults shared by challenge direction."""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from urllib.parse import urlparse

import yaml

_lock = threading.RLock()


def _load():
    path = Path('config.yaml')
    if not path.is_file():
        raise ValueError('config.yaml 不存在')
    return yaml.safe_load(path.read_text(encoding='utf-8')) or {}


def _save(config):
    config.get('llm', {}).pop('challenge_models', None)
    path = Path('config.yaml')
    temporary = path.with_suffix('.yaml.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        yaml.safe_dump(config, stream, allow_unicode=True, sort_keys=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def list_models():
    with _lock:
        return _load().get('llm', {}).get('models', [])


DIRECTIONS = ('Web', 'Pwn', 'Reverse', 'Crypto', 'Misc', 'Osint', 'AI')


def normalize_category(category):
    value = str(category or '').strip()
    aliases = {'re': 'reverse', 'reversing': 'reverse', 'cryptography': 'crypto',
               '密码学': 'crypto', '逆向': 'reverse', '杂项': 'misc', '二进制': 'pwn', '未分类': 'uncategorized'}
    key = aliases.get(value.casefold(), value.casefold())
    return key or 'uncategorized'


def category_name(category):
    key = normalize_category(category)
    if key == 'uncategorized':
        return '未分类'
    return next((name for name in DIRECTIONS if name.casefold() == key), str(category or '').strip() or '未分类')


def snapshot():
    with _lock:
        return _load().get('llm', {})


def category_defaults(category, config_snapshot=None, global_model=None):
    from config import Settings
    llm = config_snapshot if config_snapshot is not None else snapshot()
    configured = llm.get('category_models', {}).get(normalize_category(category))
    return {'default_model': configured or global_model or Settings().llm_default_model,
            'configured_default_model': configured, 'model_source': 'category' if configured else 'global',
            'model_category': category_name(category)}


def set_category_default(category, model):
    category = str(category).strip()
    if not category or len(category) > 100:
        raise ValueError('方向名称不能为空且不能超过 100 字符')
    with _lock:
        config = _load()
        llm = config.setdefault('llm', {})
        if model and not any(item.get('name') == model for item in llm.get('models', [])):
            raise ValueError(f'模型 {model} 未配置')
        defaults = llm.setdefault('category_models', {})
        key = normalize_category(category)
        if model:
            defaults[key] = model
        else:
            defaults.pop(key, None)
        _save(config)
    return category_defaults(category)


def list_category_defaults(categories=()):
    from config import Settings
    llm = snapshot()
    global_model = Settings().llm_default_model
    names = {}
    for name in (*DIRECTIONS, *categories, *llm.get('category_models', {})):
        names.setdefault(normalize_category(name), category_name(name))
    return [{'category': name, **category_defaults(name, llm, global_model)} for name in names.values()]


def _validated(values):
    item = {key: str(values.get(key) or '').strip() for key in ('name', 'provider', 'base_url', 'api_key_env')}
    if not item['name'] or len(item['name']) > 200:
        raise ValueError('模型名称不能为空且不能超过 200 字符')
    url = urlparse(item['base_url'])
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError('请输入有效的 HTTP(S) 接口地址，凭证请使用密钥环境变量')
    if item['api_key_env'] and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', item['api_key_env']):
        raise ValueError('密钥环境变量名格式无效')
    item['provider'] = item['provider'] or 'custom'
    item['base_url'] = item['base_url'].rstrip('/')
    return item


def save_model(values, original_name=None):
    item = _validated(values)
    with _lock:
        config = _load()
        llm = config.setdefault('llm', {})
        models = llm.setdefault('models', [])
        existing = next((m for m in models if m.get('name') == original_name), None) if original_name else None
        if original_name and existing is None:
            raise LookupError(f'模型 {original_name} 不存在')
        if any(m.get('name') == item['name'] and m is not existing for m in models):
            raise ValueError(f'模型 {item["name"]} 已存在')
        if existing is not None:
            existing.update(item)
            for key, value in llm.get('category_models', {}).items():
                if value == original_name:
                    llm['category_models'][key] = item['name']
        else:
            models.append(item)
        if llm.get('default_model') == original_name and original_name:
            llm.update(default_model=item['name'], base_url=item['base_url'], api_key_env=item['api_key_env'])
        _save(config)
    return item


def delete_model(name):
    from config import Settings
    with _lock:
        config = _load()
        llm = config.setdefault('llm', {})
        models = llm.setdefault('models', [])
        if not any(m.get('name') == name for m in models):
            raise LookupError(f'模型 {name} 不存在')
        if name == Settings().llm_default_model or name in llm.get('category_models', {}).values():
            raise ValueError('模型正在作为全局或方向默认模型使用，请先更换默认模型')
        llm['models'] = [m for m in models if m.get('name') != name]
        _save(config)
