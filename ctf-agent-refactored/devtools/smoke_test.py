
import asyncio, json
from config import Settings
from core.skills.local_adapter import LocalAdapter
from core.tools.local_tools import build_local_tools
from core.agent import Coordinator
from core.stats import solve_log

class FakeModel:
    def __init__(self):
        self.n = 0
        self.last_usage = {}
        self.last_model = 'fake-model'
    async def complete(self, messages, tools):
        self.n += 1
        if self.n == 1:
            self.last_usage = {'prompt_tokens': 100, 'completion_tokens': 20}
            return {'content': 'First, list files and compute something.',
                    'tool_calls': [{'id': 'c1', 'type': 'function',
                                    'function': {'name': 'run_command',
                                                 'arguments': json.dumps({'command': 'ls -la && python3 -c "print(6*7)"'})}}]}
        self.last_usage = {'prompt_tokens': 200, 'completion_tokens': 30}
        return {'content': 'CANDIDATE: 0xGame{fake_test_flag} derived from output 42.', 'tool_calls': []}

async def main():
    s = Settings()
    a = LocalAdapter(s)
    ch = await a.get_challenge('8')
    tools = build_local_tools(ch, s)
    coord = Coordinator(s, tools, model=FakeModel(), platform_id='local')
    res = await coord.solve_challenge(ch)
    await tools.cleanup()
    print('RESULT:', json.dumps(res, ensure_ascii=False)[:400])
    log = solve_log.get_solve_log('local', '8')
    print('LOG ENTRIES:', len(log['logs']))
    for item in log['logs'][-6:]:
        print(' -', item['type'], '|', item['content'][:90])

asyncio.run(main())
