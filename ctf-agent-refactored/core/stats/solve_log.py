"""Per-challenge append-only solve and agent journals with a small status index."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)
_DATA_DIR = Path(__file__).resolve().parents[2] / 'data'
_LOG_FILE = _DATA_DIR / 'solve_logs.json'
_lock = threading.RLock()


def journal_path(platform_id, challenge_id, kind='solve'):
    if kind not in ('solve', 'agent'):
        raise ValueError('Unknown journal kind')
    folder = _LOG_FILE.parent / 'challenge_logs' / ('p_' + quote(str(platform_id), safe='')) / ('c_' + quote(str(challenge_id), safe=''))
    return folder / f'{kind}.jsonl'


def _reverse_lines(path):
    if not path.exists():
        return
    with path.open('rb') as stream:
        position = stream.seek(0, 2)
        pending = b''
        while position:
            size = min(position, 65536)
            position -= size
            stream.seek(position)
            pending = stream.read(size) + pending
            lines = pending.split(b'\n')
            pending = lines.pop(0)
            for line in reversed(lines):
                if line:
                    yield line
        if pending:
            yield pending


def _entries(path, limit=None, after=0):
    if not path.exists():
        return []
    result = []
    if limit is not None:
        for line in _reverse_lines(path):
            entry = json.loads(line)
            if entry.get('sequence', 0) <= after:
                break
            result.append(entry)
            if len(result) >= limit:
                break
        return list(reversed(result))
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                entry = json.loads(line)
                if entry.get('sequence', 0) > after:
                    result.append(entry)
    return result


def _last_sequence(path):
    latest = _entries(path, 1)
    return latest[0]['sequence'] if latest else 0


def _append(path, entry):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(entry, ensure_ascii=False, default=str) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def _save(data):
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = _LOG_FILE.with_suffix('.json.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(_LOG_FILE)


def _load():
    if not _LOG_FILE.exists():
        return {'version': 2, 'sessions': {}}
    data = json.loads(_LOG_FILE.read_text(encoding='utf-8'))
    if data.get('version') != 2:
        for session in data.setdefault('sessions', {}).values():
            path = journal_path(session['platform_id'], session['challenge_id'])
            existing = _last_sequence(path)
            logs = session.pop('logs', [])
            for sequence, entry in enumerate(logs, 1):
                if sequence > existing:
                    _append(path, {**entry, 'sequence': sequence, 'run_id': 'legacy'})
            session['log_count'] = max(len(logs), existing)
            session.setdefault('agent_count', 0)
        data['version'] = 2
        _save(data)
    return data


def _session(data, platform_id, challenge_id):
    return data['sessions'].setdefault(f'{platform_id}:{challenge_id}', {
        'platform_id': platform_id, 'challenge_id': challenge_id, 'status': 'idle',
        'started_at': None, 'updated_at': None, 'log_count': 0, 'agent_count': 0,
    })


def append_log(platform_id: str, challenge_id: str, log_type: str, content: str,
               metadata: dict[str, Any] | None = None) -> None:
    with _lock:
        data = _load()
        session = _session(data, platform_id, challenge_id)
        path = journal_path(platform_id, challenge_id)
        sequence = max(session.get('log_count', 0), _last_sequence(path)) + 1
        entry = {'sequence': sequence, 'run_id': session.get('run_id', 'legacy'),
                 'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'), 'type': log_type,
                 'content': content, 'metadata': metadata or {}}
        _append(path, entry)
        session['log_count'] = sequence
        session['updated_at'] = entry['timestamp']
        session['started_at'] = session['started_at'] or entry['timestamp']
        _save(data)


def append_agent_call(platform_id, challenge_id, event, payload, *, run_id='', call_id='', round=0):
    with _lock:
        data = _load()
        session = _session(data, platform_id, challenge_id)
        path = journal_path(platform_id, challenge_id, 'agent')
        sequence = max(session.get('agent_count', 0), _last_sequence(path)) + 1
        _append(path, {'sequence': sequence, 'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                       'run_id': run_id or session.get('run_id', 'legacy'), 'call_id': call_id,
                       'round': round, 'event': event, 'payload': payload})
        session['agent_count'] = sequence
        _save(data)


def begin_run(platform_id, challenge_id, model):
    with _lock:
        data = _load()
        session = _session(data, platform_id, challenge_id)
        session.update(run_id=uuid.uuid4().hex, model=model, status='running',
                       started_at=time.strftime('%Y-%m-%d %H:%M:%S'))
        _save(data)
        message = f'开始新一轮解题（模型 {model}），历史日志保留。' if model else '开始新一轮解题，按方向选择默认模型；历史日志保留。'
        append_log(platform_id, challenge_id, 'system', message)
        return session['run_id']


def set_solve_status(platform_id, challenge_id, status, *, model=None):
    with _lock:
        data = _load()
        session = _session(data, platform_id, challenge_id)
        session['status'] = status
        if model is not None:
            session['model'] = model
        session['updated_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        _save(data)


def get_solve_log(platform_id, challenge_id, limit=None):
    with _lock:
        data = _load()
        session = dict(_session(data, platform_id, challenge_id))
        path = journal_path(platform_id, challenge_id)
        session['log_count'] = _last_sequence(path)
        session['logs'] = _entries(path, limit, session.get('display_after', 0))
        session['returned_count'] = len(session['logs'])
        session['has_older'] = session['log_count'] - session.get('display_after', 0) > len(session['logs'])
        return session


def get_all_sessions(platform_id=None):
    with _lock:
        return [dict(session) for session in _load()['sessions'].values()
                if not platform_id or session.get('platform_id') == platform_id]


def get_agent_log(platform_id, challenge_id, limit=200):
    with _lock:
        data = _load()
        session = dict(_session(data, platform_id, challenge_id))
        path = journal_path(platform_id, challenge_id, 'agent')
        session['log_count'] = _last_sequence(path)
        entries = _entries(path, limit, session.get('agent_display_after', 0))
        session['logs'] = []
        for entry in entries:
            text = json.dumps(entry['payload'], ensure_ascii=False, indent=2)
            preview = text[:12000]
            if len(text) > 12000:
                preview += '\n…预览已缩略，下载 Agent 日志查看完整内容。'
            session['logs'].append({'sequence': entry['sequence'], 'timestamp': entry['timestamp'],
                'type': entry['event'], 'content': preview,
                'metadata': {key: entry[key] for key in ('run_id', 'call_id', 'round')}})
        session['returned_count'] = len(entries)
        session['has_older'] = session['log_count'] - session.get('agent_display_after', 0) > len(entries)
        return session


def clear_solve_log(platform_id, challenge_id):
    """Reset the visible window while keeping both downloadable journals intact."""
    with _lock:
        data = _load()
        session = _session(data, platform_id, challenge_id)
        session['display_after'] = _last_sequence(journal_path(platform_id, challenge_id))
        session['agent_display_after'] = _last_sequence(journal_path(platform_id, challenge_id, 'agent'))
        session['status'] = 'idle'
        _save(data)


def iter_journal(platform_id, challenge_id, kind):
    with _lock:
        _load()
    path = journal_path(platform_id, challenge_id, kind)
    if path.exists():
        with path.open('rb') as stream:
            while chunk := stream.read(65536):
                yield chunk
