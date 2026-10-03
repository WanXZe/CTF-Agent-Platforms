import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from config import Settings
from core.agent import solve_runner as runner, model_config
from core.agent.solve_options import MIN_TOKEN_BUDGET, MAX_TOKEN_BUDGET, validate_token_budget
from core.agent.local_llm import LocalModel
from core.models import Challenge
from core.stats import solve_log
from core.tools import ToolRegistry
from core.tools import local_tools
from core.tools.local_tools import Workspace, stage_workspace
from web.routes.platform import SolveRequest, router


class BudgetValidationTests(unittest.TestCase):
    def test_limits_and_default(self):
        for value in [None, MIN_TOKEN_BUDGET, 500000, MAX_TOKEN_BUDGET]:
            self.assertEqual(validate_token_budget(value), value)
            self.assertEqual(SolveRequest(challenge_id='x', token_budget=value).token_budget, value)
        for value in [-1, 0, MIN_TOKEN_BUDGET-1, MAX_TOKEN_BUDGET+1, True, '100000', 100000.5]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):validate_token_budget(value)
                with self.assertRaises(ValueError):SolveRequest(challenge_id='x', token_budget=value)


class BudgetApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_rejects_invalid_budget_before_start(self):
        app=FastAPI();app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            with patch.object(runner,'start_solve') as start:
                for value in [0,-1,MAX_TOKEN_BUDGET+1,False,'200000',1000.5]:
                    response=await client.post('/api/solve/local/unit',json={'challenge_id':'unit','token_budget':value})
                    self.assertEqual(response.status_code,422)
                start.assert_not_called()

    async def test_api_passes_budget_and_advertises_default(self):
        app=FastAPI();app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            with patch('web.deps.get_adapter_by_id',return_value=object()),patch.object(runner,'is_running',return_value=False),patch.object(runner,'start_solve',return_value=True) as start:
                response=await client.post('/api/solve/local/unit',json={'challenge_id':'unit','token_budget':500000})
                self.assertEqual(response.status_code,200)
                self.assertEqual(start.call_args.kwargs['token_budget'],500000)
            response=await client.get('/api/models')
            self.assertEqual(response.json()['solve_options']['token_budget'],Settings().solver_token_budget)
            self.assertEqual(response.json()['solve_options']['max_token_budget'],MAX_TOKEN_BUDGET)


class BudgetSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_budget_is_per_run_and_retained_in_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings=Settings(_env_file=None,llm_default_model='unit',solver_token_budget=100000)
            config={'default_model':'unit','models':[{'name':'unit','base_url':'http://127.0.0.1:1/v1','api_key_env':''}]}
            adapter=AsyncMock();adapter.get_challenge.return_value=Challenge(id='unit',name='unit',category='Reverse')
            budgets=[]
            class Coordinator:
                def __init__(self,settings,*args,**kwargs):budgets.append(settings.solver_token_budget)
                async def solve_challenge(self,challenge):return {'status':'needs_human'}
            tools=ToolRegistry();tools.cleanup=AsyncMock()
            try:
                with patch.object(solve_log,'_LOG_FILE',Path(temporary)/'logs.json'),patch.object(runner,'Settings',side_effect=lambda:settings.model_copy()),patch.object(model_config,'snapshot',return_value=config),patch.object(runner,'build_local_tools',return_value=tools),patch.object(runner,'Coordinator',Coordinator):
                    for requested,expected in [(500000,500000),(None,100000)]:
                        self.assertTrue(runner.start_solve('local','unit',adapter,token_budget=requested))
                        async with asyncio.timeout(3):
                            while runner.is_running('local','unit'):await asyncio.sleep(.005)
                        data=solve_log.get_solve_log('local','unit')
                        self.assertEqual(data['token_budget'],expected)
                    self.assertEqual(budgets,[500000,100000])
                    self.assertEqual(settings.solver_token_budget,100000)
                    self.assertNotIn('token_budget',config)
                    self.assertIn('500,000',b''.join(solve_log.iter_journal('local','unit','solve')).decode())
                    self.assertEqual(tools.cleanup.await_count,2)
            finally:
                await runner.shutdown_tasks()


class FreshWorkspaceTests(unittest.TestCase):
    def test_fresh_run_never_reuses_outputs_and_source_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);source=folder/'source';source.mkdir()
            (source/'attachment.zip').write_bytes(b'original')
            (source/'QuestionInfo.json').write_text('secret answer')
            (source/'WriteUp.md').write_text('answer')
            with patch.object(local_tools,'_WORKSPACE_HOME',folder/'workspaces'):
                first=stage_workspace(source,'same-question')
                (first/'attachment.zip').write_bytes(b'modified')
                (first/'unpacked.exe').write_bytes(b'generated')
                (first/'solve.py').write_text('generated')
                second=stage_workspace(source,'same-question')
                self.assertNotEqual(first,second)
                self.assertEqual((second/'attachment.zip').read_bytes(),b'original')
                self.assertFalse((second/'unpacked.exe').exists())
                self.assertFalse((second/'solve.py').exists())
                self.assertFalse((second/'QuestionInfo.json').exists())
                self.assertFalse((second/'WriteUp.md').exists())
                self.assertTrue((first/'unpacked.exe').exists())
                self.assertEqual((source/'attachment.zip').read_bytes(),b'original')

    def test_containers_are_unique_even_for_the_same_long_workspace_name(self):
        settings=SimpleNamespace(sandbox_mode='docker',sandbox_image='unit')
        first=Workspace(Path('/tmp/'+'x'*100),settings)
        second=Workspace(first.root,settings)
        self.assertNotEqual(first._container_name(),second._container_name())
        self.assertEqual(first._container_name(),first._container_name())
        self.assertLess(len(first._container_name()),64)


class ContinuationTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_continue_then_new_restores_only_explicitly_selected_history_and_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);source=folder/'source';source.mkdir();(source/'attachment.txt').write_text('original')
            settings=Settings(_env_file=None,llm_default_model='unit',sandbox_mode='host')
            config={'models':[{'name':'unit','base_url':'http://127.0.0.1:1/v1','api_key_env':''}]}
            adapter=AsyncMock();adapter.get_challenge.return_value=Challenge(id='unit',name='unit',category='Reverse',raw={'local_dir':str(source)})
            seen=[]
            async def complete(model,messages,schemas):
                seen.append(json.loads(json.dumps(messages)))
                if len(seen)==1:
                    return {'tool_calls':[{'id':'write','type':'function','function':{'name':'write_file','arguments':json.dumps({'path':'progress.txt','content':'prior progress'})}}]}
                return {'content':'Need more work'}
            try:
                with patch.object(solve_log,'_LOG_FILE',folder/'logs.json'),patch.object(local_tools,'_WORKSPACE_HOME',folder/'runs'),patch.object(runner,'Settings',side_effect=lambda:settings.model_copy()),patch.object(model_config,'snapshot',return_value=config),patch.object(LocalModel,'complete',complete):
                    async def launch(mode):
                        self.assertTrue(runner.start_solve('local','unit',adapter,start_mode=mode))
                        async with asyncio.timeout(3):
                            while runner.is_running('local','unit'):await asyncio.sleep(.005)
                        data=solve_log.get_solve_log('local','unit')
                        self.assertEqual(data['status'],'needs_human')
                        return data
                    first=await launch('new');root=Path(first['workspace'])
                    self.assertTrue((root/'progress.txt').is_file())
                    solve_log.clear_solve_log('local','unit')
                    second=await launch('continue')
                    self.assertEqual(second['workspace'],str(root))
                    self.assertTrue((root/'progress.txt').is_file())
                    self.assertIn('previous_solve_log',seen[2][2]['content'])
                    self.assertIn('progress.txt',seen[2][2]['content'])
                    third=await launch('new')
                    self.assertNotEqual(third['workspace'],str(root))
                    self.assertFalse((Path(third['workspace'])/'progress.txt').exists())
                    self.assertEqual(len(seen[3]),2)
                    self.assertFalse(any('写入 progress.txt' in e['content'] for e in third['logs']))
                    self.assertIn('写入 progress.txt',b''.join(solve_log.iter_journal('local','unit','solve')).decode())
                    self.assertTrue(list(solve_log.journal_path('local','unit').parent.glob('archives/*/solve.jsonl')))
            finally:await runner.shutdown_tasks()

    async def test_history_context_is_bounded_and_empty_history_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(solve_log,'_LOG_FILE',Path(temporary)/'logs.json'):
            with self.assertRaisesRegex(ValueError,'没有可继续'):
                solve_log.resume_snapshot('local','empty')
            solve_log.begin_run('local','unit','unit',start_mode='new')
            for index in range(200):solve_log.append_log('local','unit','output',f'item-{index} '+'x'*5000)
            history=solve_log.resume_snapshot('local','unit',max_chars=10000)
            self.assertLessEqual(len(history['history']),10000)
            self.assertIn('item-199',history['history'])
            self.assertIn('本条长输出已截断',history['history'])

    async def test_resume_workspace_cannot_escape_managed_workspace_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);source=folder/'source';source.mkdir()
            challenge=Challenge(id='unit',name='unit',raw={'local_dir':str(source)})
            with patch.object(local_tools,'_WORKSPACE_HOME',folder/'runs'):
                with self.assertRaisesRegex(ValueError,'路径不安全'):
                    local_tools.build_local_tools(challenge,SimpleNamespace(sandbox_mode='host'),resume_root=source)

    async def test_full_download_snapshot_survives_a_concurrent_fresh_start(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(solve_log,'_LOG_FILE',Path(temporary)/'logs.json'):
            solve_log.append_log('local','unit','output','old-output-'+'x'*200000)
            download=solve_log.iter_journal('local','unit','solve')
            first=next(download)
            solve_log.begin_run('local','unit','unit',start_mode='new')
            original=first+b''.join(download)
            entries=[json.loads(line) for line in original.decode().splitlines()]
            self.assertEqual(len(entries),1)
            self.assertEqual(len(entries[0]['content']),200011)
            self.assertIn('old-output-',b''.join(solve_log.iter_journal('local','unit','solve')).decode())


class SandboxLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_mode_does_not_fall_back_to_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace=Workspace(Path(temporary),SimpleNamespace(sandbox_mode='auto',sandbox_image='missing'))
            workspace._image_present=AsyncMock(return_value=False)
            workspace._host_run=AsyncMock()
            result=await workspace.run('echo should-not-execute')
            self.assertFalse(result.success)
            self.assertIn('不会回退',result.message)
            workspace._host_run.assert_not_awaited()
            await workspace.close()

    async def test_preparation_and_cleanup_are_logged_and_commands_reuse_one_run_container(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            workspace=Workspace(root,SimpleNamespace(sandbox_mode='docker',sandbox_image='unit'))
            commands=[]
            async def fake(args,timeout=60,cwd=None,output_paths=None):
                commands.append(args)
                if output_paths:
                    output_paths[0].write_text('done');output_paths[1].write_text('')
                return 0,('container-id' if args[1]=='run' else ''),''
            workspace._run=fake;workspace.audit_context=('local','unit')
            with patch.object(solve_log,'_LOG_FILE',root/'logs.json'):
                await workspace.prepare()
                self.assertTrue(workspace.container)
                self.assertTrue((await workspace.run('first')).success)
                self.assertTrue((await workspace.run('second')).success)
                await workspace.close()
                data=solve_log.get_solve_log('local','unit')
                self.assertEqual(sum(args[1]=='run' for args in commands),1)
                self.assertTrue(any('沙箱已启动' in entry['content'] for entry in data['logs']))
                self.assertTrue(any('沙箱已清理' in entry['content'] for entry in data['logs']))
                self.assertEqual(workspace.container,'')


if __name__=='__main__':unittest.main()
