"""Regression tests: explicit public proxy, LAN bypass and actionable failures."""
import unittest
from unittest.mock import patch

import httpx
from config import Settings
from core.agent.local_llm import LocalModel


class ModelProxyTests(unittest.TestCase):
    def model(self, base='https://api.deepseek.com/v1', proxy='socks5h://192.168.174.1:10808'):
        return LocalModel(Settings(_env_file=None,llm_base_url=base,llm_proxy_url=proxy))

    def test_explicit_public_proxy_is_passed_to_client(self):
        model=self.model()
        with patch('core.agent.local_llm.httpx.AsyncClient') as client:
            model._http();model._http()
            client.assert_called_once()
            self.assertEqual(client.call_args.kwargs['proxy'],'socks5h://192.168.174.1:10808')
            self.assertFalse(client.call_args.kwargs['trust_env'])

    def test_lan_loopback_ipv6_bypass(self):
        for base in ['http://127.0.0.1:11434/v1','http://localhost:11434/v1','http://[::1]:11434/v1',
                     'http://192.168.174.1:11434/v1','http://10.0.0.2/v1','http://172.16.0.2/v1','http://server.local/v1']:
            with self.subTest(base=base):
                model=self.model(base)
                self.assertEqual(model._proxy_url(),'')
                with patch('core.agent.local_llm.httpx.AsyncClient') as client:
                    model._http()
                    self.assertNotIn('proxy',client.call_args.kwargs)

    def test_no_proxy_remains_direct(self):
        self.assertEqual(self.model(proxy='')._proxy_url(),'')

    def test_invalid_proxy_rejected(self):
        with self.assertRaisesRegex(ValueError,'模型代理'):
            self.model(proxy='ftp://proxy')._proxy_url()

    def test_missing_socks_dependency_actionable(self):
        with patch('core.agent.local_llm.httpx.AsyncClient',side_effect=ImportError('missing socksio')):
            with self.assertRaisesRegex(ValueError,r'httpx\[socks\]'):
                self.model()._http()

    def test_loopback_error_explains_vm_boundary(self):
        error=self.model('http://127.0.0.1:11434/v1')._connection_error(httpx.ConnectError('refused'))
        self.assertIn('不是浏览器所在的宿主机',str(error))

    def test_proxy_error_is_connection_failure(self):
        model=self.model(proxy='socks5h://user:secret@192.168.174.1:10808')
        error=model._connection_error(httpx.ProxyError('failed'))
        self.assertIn('模型连接失败',str(error))
        self.assertNotIn('secret',str(error))


if __name__=='__main__':unittest.main()
