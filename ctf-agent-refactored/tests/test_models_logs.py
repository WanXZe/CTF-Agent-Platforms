"""Persistence, API and real solve-loop regressions without paid model calls."""
import asyncio
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import yaml
from fastapi import FastAPI

from config import Settings
from core.agent import model_config, solve_runner
from core.agent.coordinator import SolveSession
from core.agent.local_llm import LocalModel
from core.models import Challenge
from core.stats import solve_log
from core.tools import ToolRegistry
from core.tools.local_tools import Workspace
from web.deps import get_platform_adapter
from web.routes.challenges import router as challenge_router
from web.routes.platform import router


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_cwd = Path.cwd()
        os.chdir(self.root)
        config = {'llm': {'default_model': 'model-a', 'base_url': 'http://127.0.0.1:1111/v1', 'models': [
            {'name': 'model-a', 'provider': 'local', 'base_url': 'http://127.0.0.1:1111/v1', 'api_key_env': ''},
            {'name': 'model-b', 'provider': 'local', 'base_url': 'http://127.0.0.1:2222/v1', 'api_key_env': 'MODEL_B_KEY'},
        ]}}
        (self.root / 'config.yaml').write_text(yaml.safe_dump(config), encoding='utf-8')
        self.logpatch = patch.object(solve_log, '_LOG_FILE', self.root / 'data' / 'logs.json')
        self.logpatch.start()
        self.statpatch = patch('core.stats.token_stats.record_token_usage')
        self.statpatch.start()
        self.adapter = AsyncMock()
        self.adapter.get_challenge.side_effect = lambda id: Challenge(id=id, name='Test', category='Reverse')
        self.adapter.list_challenges.return_value = [Challenge(id='1', name='Test', category='Reverse'), Challenge(id='2', name='Other', category='reverse'), Challenge(id='3', name='Web', category='Web')]
        self.app = FastAPI()
        self.app.include_router(router)
        self.app.include_router(challenge_router)
        self.app.dependency_overrides[get_platform_adapter] = lambda: self.adapter
        self.adapterpatch = patch('web.deps.get_adapter_by_id', return_value=self.adapter)
        self.adapterpatch.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test', headers={'X-Platform-Id': 'local'})

    async def asyncTearDown(self):
        await solve_runner.shutdown_tasks()
        await asyncio.sleep(0)
        await self.client.aclose()
        self.adapterpatch.stop()
        self.statpatch.stop()
        self.logpatch.stop()
        os.chdir(self.old_cwd)
        self.temp.cleanup()

    async def test_direction_defaults_apply_to_all_questions_and_platforms(self):
        response = await self.client.put('/api/category-models/Reverse', json={'model': 'model-b'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(model_config.category_defaults('RE')['default_model'], 'model-b')
        self.assertEqual(model_config.category_defaults('Web')['default_model'], 'model-a')
        rows = (await self.client.get('/api/challenges')).json()['data']
        self.assertEqual([r['default_model'] for r in rows], ['model-b', 'model-b', 'model-a'])
        other = (await self.client.get('/api/challenges', headers={'X-Platform-Id': 'other'})).json()['data']
        self.assertEqual(other[0]['default_model'], 'model-b')
        detail = (await self.client.get('/api/challenges/1')).json()['data']
        self.assertEqual(detail['configured_default_model'], 'model-b')
        self.assertEqual(detail['model_source'], 'category')
        self.assertEqual((await self.client.put('/api/category-models/Reverse', json={'model': 'missing'})).status_code, 400)
        self.assertEqual(model_config.category_defaults('Reverse')['default_model'], 'model-b')
        directions = (await self.client.get('/api/category-models')).json()['data']
        self.assertEqual(next(row for row in directions if row['category'] == 'Reverse')['default_model'], 'model-b')
        self.assertEqual((await self.client.put('/api/challenge-model/local/1', json={'model': 'model-b'})).status_code, 404)
        await self.client.put('/api/category-models/Reverse', json={'model': None})
        self.assertEqual(model_config.category_defaults('Reverse')['default_model'], 'model-a')

    async def test_edit_and_rename_updates_defaults_and_connection(self):
        model_config.set_category_default('Reverse', 'model-b')
        updated = {'name': 'renamed-b', 'provider': 'updated', 'base_url': 'http://127.0.0.1:3333/v1', 'api_key_env': 'NEW_KEY'}
        response = await self.client.put('/api/models/model-b', json=updated)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(model_config.category_defaults('Reverse')['default_model'], 'renamed-b')
        selected = solve_runner.selected_settings('renamed-b')
        self.assertEqual(selected.llm_base_url, updated['base_url'])
        self.assertEqual(selected.llm_api_key_env, 'NEW_KEY')
        self.assertEqual((await self.client.delete('/api/models/renamed-b')).status_code, 409)
        duplicate = await self.client.put('/api/models/renamed-b', json={**updated, 'name': 'model-a'})
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual((await self.client.put('/api/models/missing', json=updated)).status_code, 404)
        self.assertEqual((await self.client.put('/api/models/renamed-b', json={**updated, 'base_url': 'file:///bad'})).status_code, 400)
        await self.client.put('/api/models/model-a', json={**updated, 'name': 'renamed-a'})
        self.assertEqual((await self.client.get('/api/models')).json()['default'], 'renamed-a')
        self.assertEqual(solve_runner.selected_settings().llm_base_url, updated['base_url'])

    async def test_full_journals_tail_window_and_clear_only_display(self):
        long_text = '完整内容' * 6000
        for i in range(350):
            solve_log.append_log('local', '1', 'output', long_text if i == 0 else str(i))
        solve_log.begin_run('local', '1', 'model-b')
        solve_log.append_agent_call('local', '1', 'model.request', {'messages': [{'content': long_text}]}, call_id='call')
        response = await self.client.get('/api/solve-log/local/1')
        data = response.json()['data']
        self.assertEqual(len(data['logs']), 200)
        self.assertEqual(data['log_count'], 351)
        self.assertEqual(data['logs'][0]['sequence'], 152)
        self.assertTrue(data['has_older'])
        download = await self.client.get('/api/solve-log/local/1/download')
        lines = [json.loads(line) for line in download.text.splitlines()]
        self.assertEqual(len(lines), 351)
        self.assertEqual(lines[0]['content'], long_text)
        agent = await self.client.get('/api/solve-log/local/1/download?kind=agent')
        self.assertEqual(json.loads(agent.text)['payload']['messages'][0]['content'], long_text)
        preview = (await self.client.get('/api/solve-log/local/1?kind=agent')).json()['data']['logs'][0]
        self.assertIn('预览已缩略', preview['content'])
        for query in ['limit=0', 'limit=2001', 'kind=bad']:
            self.assertEqual((await self.client.get('/api/solve-log/local/1?' + query)).status_code, 422)
        await self.client.delete('/api/solve-log/local/1')
        self.assertEqual((await self.client.get('/api/solve-log/local/1')).json()['data']['logs'], [])
        self.assertEqual(len((await self.client.get('/api/solve-log/local/1/download')).text.splitlines()), 351)
        solve_log.begin_run('local', '1', 'model-a')
        self.assertEqual(solve_log.get_solve_log('local', '1')['log_count'], 352)

    async def test_legacy_migration_and_concurrent_append(self):
        solve_log._LOG_FILE.parent.mkdir(parents=True)
        legacy = {'sessions': {'local:1': {'platform_id': 'local', 'challenge_id': '1', 'status': 'failed',
            'started_at': 'old', 'updated_at': 'old', 'logs': [{'timestamp': 'old', 'type': 'output', 'content': '旧记录' * 20000, 'metadata': {}} for _ in range(3)]}}}
        solve_log._LOG_FILE.write_text(json.dumps(legacy), encoding='utf-8')
        self.assertEqual(len(solve_log.get_solve_log('local', '1')['logs']), 3)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: solve_log.append_log('local', '1', 'output', str(i)), range(100)))
        logs = solve_log.get_solve_log('local', '1')['logs']
        self.assertEqual([log['sequence'] for log in logs], list(range(1, 104)))
        self.assertEqual(logs[0]['content'], '旧记录' * 20000)
        self.assertEqual(solve_log.get_solve_log('other', '1')['logs'], [])
        self.assertEqual(json.loads(solve_log._LOG_FILE.read_text())['version'], 2)

    async def test_agent_request_response_and_full_command_output(self):
        if os.name != 'posix':
            self.skipTest('Uses the VM shell backend')
        workspace = Workspace(self.root, Settings(_env_file=None, sandbox_mode='host'))
        workspace.audit_context = ('local', '1')
        tools = ToolRegistry()
        tools.register('run_command', workspace.run)
        replies = iter([
            {'tool_calls': [{'id': 'tool1', 'type': 'function', 'function': {'name': 'run_command', 'arguments': json.dumps({'command': "python3 -c \"print('X'*25000)\""})}}]},
            {'content': '完整回复' * 3000, 'reasoning_content': '独立分析' * 3000},
        ])
        class Model:
            async def complete(self, *_):
                return next(replies)
        session = SolveSession(Challenge(id='1', name='Test'), tools, Settings(_env_file=None, solver_max_rounds=3), Model())
        result = await session.run()
        self.assertEqual(result['status'], 'needs_human')
        entries = [json.loads(line) for line in solve_log.journal_path('local', '1', 'agent').read_text().splitlines()]
        self.assertEqual([entry['event'] for entry in entries], ['model.request', 'model.response', 'tool.request', 'tool.response', 'model.request', 'model.response'])
        self.assertEqual(entries[0]['call_id'], entries[1]['call_id'])
        self.assertEqual(entries[-1]['payload']['message']['content'], '完整回复' * 3000)
        logs = solve_log.get_solve_log('local', '1')['logs']
        output = ''.join(entry['content'] for entry in logs if entry['metadata'].get('stream') == 'stdout')
        self.assertEqual(output, 'X' * 25000 + '\n')
        self.assertIn('完整回复' * 3000, [entry['content'] for entry in logs])
        self.assertIn('独立分析' * 3000, [entry['content'] for entry in logs])
        await workspace.close()

    async def test_default_precedence_and_running_settings_snapshot(self):
        model_config.set_category_default('Reverse', 'model-b')
        entered, release = asyncio.Event(), asyncio.Event()
        observed = []
        async def complete(model, *_):
            observed.append((model.settings.llm_default_model, model.settings.llm_base_url))
            entered.set()
            await release.wait()
            return {'content': 'Done'}
        with patch.object(LocalModel, 'complete', complete), patch.object(solve_runner, 'build_local_tools', return_value=ToolRegistry()):
            solve_log.append_log('local', '1', 'system', 'previous attempt')
            self.assertTrue(solve_runner.start_solve('local', '1', self.adapter))
            model_config.set_category_default('Reverse', 'model-a')
            model_config.save_model({'name': 'renamed-b', 'base_url': 'http://127.0.0.1:4444/v1'}, 'model-b')
            await entered.wait()
            release.set()
            await solve_runner._tasks['local:1']
            await asyncio.sleep(0)
            self.assertEqual(observed[0], ('model-b', 'http://127.0.0.1:2222/v1'))
            self.assertTrue(solve_runner.start_solve('local', '1', self.adapter, model='model-a'))
            await solve_runner._tasks['local:1']
            await asyncio.sleep(0)
        self.assertEqual(observed[-1][0], 'model-a')
        self.assertIn('previous attempt', [entry['content'] for entry in solve_log.get_solve_log('local', '1')['logs']])

    async def test_old_question_defaults_are_ignored_and_unknown_directions_fall_back(self):
        config = yaml.safe_load((self.root / 'config.yaml').read_text())
        config['llm']['challenge_models'] = {'local:1': 'model-b'}
        (self.root / 'config.yaml').write_text(yaml.safe_dump(config))
        self.assertEqual((await self.client.get('/api/challenges/1')).json()['data']['default_model'], 'model-a')
        self.assertEqual(model_config.category_defaults('New Direction')['default_model'], 'model-a')
        model_config.set_category_default('New Direction', 'model-b')
        self.assertEqual(model_config.category_defaults('new direction')['default_model'], 'model-b')
        self.assertEqual(model_config.category_defaults('')['model_category'], '未分类')

    async def test_connection_check_uses_direction_default_or_explicit_override(self):
        model_config.set_category_default('Reverse', 'model-b')
        observed = []
        async def probe(model):
            observed.append(model.settings.llm_default_model)
            return {'available': True, 'message': 'mock'}
        with patch.object(LocalModel, 'probe', probe):
            self.assertEqual((await self.client.post('/api/models/check', json={'challenge_id':'1'})).status_code,200)
            self.assertEqual((await self.client.post('/api/models/check', json={'challenge_id':'1','model':'model-a'})).status_code,200)
        self.assertEqual(observed,['model-b','model-a'])

    async def test_edit_model_names_containing_slashes(self):
        values = {'name':'org/model','base_url':'http://127.0.0.1:5555/v1','provider':'test','api_key_env':'TEST_KEY'}
        self.assertEqual((await self.client.post('/api/models',json=values)).status_code,200)
        response = await self.client.put('/api/models/org%2Fmodel',json={**values,'provider':'edited'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['data']['provider'],'edited')
        self.assertEqual((await self.client.delete('/api/models/org%2Fmodel')).status_code,200)

    async def test_failed_model_call_is_audited_without_auth_headers(self):
        settings = Settings(_env_file=None, llm_api_key='DO_NOT_LOG_THIS_SECRET')
        model = LocalModel(settings)
        model._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(402)))
        session = SolveSession(Challenge(id='1', name='Test'), ToolRegistry(), settings, model)
        await session.run()
        text = solve_log.journal_path('local', '1', 'agent').read_text()
        self.assertIn('model.error', text)
        self.assertNotIn('DO_NOT_LOG_THIS_SECRET', text)
        self.assertNotIn('Authorization', text)
        await model.aclose()


if __name__ == '__main__':
    unittest.main()
