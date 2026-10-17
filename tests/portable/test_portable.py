import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'skills/video-factory-setup/scripts'))
from portable_n8n import API, Fault, origin, provision, locked, install_local
from n8n_workflows import probe_workflow, production_workflow, schema_workflow, guard_workflow, recovery_workflow, setup_hook

class FakeAPI:
    url='https://n8n.example.invalid'
    def __init__(self): self.posts=[]; self.workflows={}; self.fail=False
    def preflight(self): return {}
    def request(self,method,path,value=None):
        if method=='POST':
            self.posts.append((path,value))
            if self.fail: raise Fault('N8N_MUTATION_UNKNOWN_DO_NOT_RETRY')
            id=str(len(self.posts))
            if path.endswith('workflows'): self.workflows[id]={**value,'active':False}
            return {'id':id}
        return self.workflows[path.rsplit('/',1)[1]]

class Portable(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.api=FakeAPI(); self.workflow=probe_workflow('baseTest','tableTest')
        self.credentials={'httpCustomAuth':{'type':'httpCustomAuth','data':{'json':'SECRET_SENT_ONLY_TO_N8N'}}}
    def tearDown(self): self.tmp.cleanup()
    def connect(self): return provision(self.api,self.root,'sample',self.workflow,self.credentials)
    def test_import_resume_does_not_duplicate_or_store_secrets(self):
        first=self.connect(); self.assertEqual(first,self.connect()); self.assertEqual(len(self.api.posts),2)
        self.assertNotIn('SECRET_SENT_ONLY_TO_N8N',(self.root/'binding-sample.json').read_text(encoding='utf-8'))
        self.assertEqual(self.api.posts[1][1]['nodes'][1]['credentials']['httpCustomAuth']['id'],'1')
    def test_uncertain_mutation_blocks_replay(self):
        self.api.fail=True
        with self.assertRaises(Fault): self.connect()
        self.api.fail=False
        with self.assertRaisesRegex(Fault,'UNCERTAIN'): self.connect()
        self.assertEqual(len(self.api.posts),1)
    def test_changed_origin_blocks_mutation(self):
        self.connect(); self.api.url='https://another.example.invalid'
        with self.assertRaisesRegex(Fault,'BINDING_CHANGED'): self.connect()
        self.assertEqual(len(self.api.posts),2)
    def test_changed_base_requires_separate_project(self):
        self.connect(); self.workflow=probe_workflow('baseOther','tableOther')
        with self.assertRaisesRegex(Fault,'BINDING_CHANGED'): self.connect()
    def test_drift_and_active_workflow_not_overwritten(self):
        result=self.connect(); self.api.workflows[result['workflow']]['active']=True
        with self.assertRaisesRegex(Fault,'ALREADY_ACTIVE'): self.connect()
        self.assertEqual(len(self.api.posts),2)
    def test_lock_excludes_parallel_installers(self):
        with locked(self.root):
            with self.assertRaisesRegex(Fault,'LOCKED'): self.connect()
        self.connect()
    def test_unsafe_origins_rejected_before_key_send(self):
        for url in ['http://remote.invalid','https://user:pass@host.invalid','https://host.invalid/?key=secret','https://host.invalid/path','file:///tmp/a']:
            with self.subTest(url=url),self.assertRaises(Fault): origin(url)
        self.assertEqual(origin('http://127.0.0.1:5678/'),'http://127.0.0.1:5678')
    def test_occupied_local_port_never_overwritten(self):
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            with self.assertRaisesRegex(Fault,'PORT_IN_USE'):
                install_local(self.root,'node',sock.getsockname()[1])
        self.assertFalse((self.root/'local.json').exists())
    def test_nonempty_install_directory_never_overwritten(self):
        path=self.root/'package.json'; path.write_text('{"name":"existing-project"}')
        with self.assertRaisesRegex(Fault,'DIRECTORY_NOT_EMPTY'):
            install_local(self.root,'node',15679)
        self.assertEqual(path.read_text(encoding='utf-8'),'{"name":"existing-project"}')
    def test_paid_templates_never_retry_or_activate(self):
        for flow in ['script','video-submit','video-poll']:
            workflow=production_workflow('baseTest','tableTest',flow)
            self.assertFalse(workflow['active'])
            self.assertEqual(workflow['nodes'][0]['type'],'n8n-nodes-base.manualTrigger')
            for node in workflow['nodes']:
                if node['type']=='n8n-nodes-base.httpRequest': self.assertFalse(node['retryOnFail'])
    def test_all_generated_code_nodes_parse_in_node(self):
        import subprocess
        workflows=[probe_workflow('baseTest','tableTest'),schema_workflow('baseTest','tableTest')]
        workflows += [production_workflow('baseTest','tableTest',flow) for flow in ['script','video-submit','video-poll']]
        workflows += [guard_workflow(production_workflow('baseTest','tableTest','script'),{'table':'ledgerTest','row':1}),recovery_workflow('baseTest','tableTest',{'table':'ledgerTest','row':1})]
        source=[node['parameters']['jsCode'] for workflow in workflows for node in workflow['nodes'] if node['type']=='n8n-nodes-base.code']
        result=subprocess.run(['node','-e',"let s='';process.stdin.on('data',c=>s+=c);process.stdin.on('end',()=>{for(const code of JSON.parse(s))new Function('$json','$input','$','$execution',code);});"],input=json.dumps(source),text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
    def test_private_node_archive_rejects_escape_paths(self):
        from node_runtime import destination
        for name in ['/etc/passwd','node/../../secret','other/bin/node','node/../secret','node/C:/secret','node/dir\\secret']:
            with self.subTest(name=name),self.assertRaises(Fault): destination(self.root,name,'node')
        self.assertEqual(destination(self.root,'node/bin/node','node'),self.root/'node/bin/node')
    def test_setup_hook_rejects_paid_workflows(self):
        for flow in ['script','video-submit','video-poll']:
            with self.assertRaisesRegex(ValueError,'MUST_NOT_CALL_MODELS'):
                setup_hook(production_workflow('baseTest','tableTest',flow),'test-hook')
    def test_dependency_timeout_has_private_diagnostics(self):
        from portable_n8n import run_checked
        with self.assertRaisesRegex(Fault,'TIMEOUT') as result:
            run_checked([sys.executable,'-c',"import time; print('SYNTHETIC_PRIVATE_OUTPUT',flush=True); time.sleep(30)"],self.root,timeout=1)
        self.assertIn(b'SYNTHETIC_PRIVATE_OUTPUT',result.exception.diagnostics)
        self.assertNotIn('SYNTHETIC_PRIVATE_OUTPUT',str(result.exception))
    def test_paid_export_cannot_bypass_durable_ledger(self):
        import contextlib,io
        from portable_n8n import main
        output=self.root/'export.json'
        args=['export','--base','baseTest','--table','tableTest','--flow','script','--out',str(output)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(args),2)
            self.assertEqual(main(args+['--experimental-manual']),2)
            self.assertFalse(output.exists())
            self.assertEqual(main(args+['--experimental-manual','--ledger-table','ledgerTest','--ledger-row','1']),0)
        self.assertIn('Claim project execution',[n['name'] for n in json.loads(output.read_text(encoding='utf-8'))['nodes']])
    def test_new_instance_never_inherits_an_existing_database(self):
        from portable_n8n import environment
        with patch.dict('os.environ',{'DB_TYPE':'postgresdb','DB_POSTGRESDB_HOST':'existing.internal','N8N_ENCRYPTION_KEY':'EXISTING_KEY','QUEUE_BULL_REDIS_HOST':'existing.internal','NODE_OPTIONS':'--require=other-project.js'}):
            env=environment(self.root,15679)
        for key in ['DB_TYPE','DB_POSTGRESDB_HOST','N8N_ENCRYPTION_KEY','QUEUE_BULL_REDIS_HOST','NODE_OPTIONS']:
            self.assertNotIn(key,env)
        self.assertEqual(env['N8N_PORT'],'15679')
        self.assertEqual(env['N8N_USER_FOLDER'],str(self.root/'data'))
    def test_project_keys_prompt_once_and_reuse_exact_references(self):
        from portable_n8n import project_credentials
        prompts=[]
        def ask(prompt): prompts.append(prompt); return 'SYNTHETIC_PRIVATE_KEY'
        first=project_credentials(self.api,self.root,'sample','cliFixture',['feishu','script','video'],ask)
        second=project_credentials(self.api,self.root,'sample','cliFixture',['feishu','script','video'],ask)
        self.assertEqual(first,second); self.assertEqual(len(prompts),3); self.assertEqual(len(self.api.posts),3)
        self.assertNotIn('SYNTHETIC_PRIVATE_KEY',(self.root/'credentials-sample.json').read_text(encoding='utf-8'))
        provision(self.api,self.root,'sample-probe',self.workflow,{'httpCustomAuth':first['feishu']})
        self.assertEqual(len(self.api.posts),4)
        self.assertEqual(self.api.posts[-1][1]['nodes'][1]['credentials']['httpCustomAuth'],first['feishu']['existing'])
        with self.assertRaisesRegex(Fault,'SCOPE_CHANGED'):
            project_credentials(self.api,self.root,'sample','cliOther',['feishu'],ask)
        self.assertEqual(len(prompts),3)
    def test_new_reference_is_never_silently_ignored_on_resume(self):
        self.connect()
        with self.assertRaisesRegex(Fault,'CREDENTIAL_REFERENCE_CHANGED'):
            provision(self.api,self.root,'sample',self.workflow,{'httpCustomAuth':{'existing':{'id':'other','name':'other'}}})
        self.assertEqual(len(self.api.posts),2)
    def test_provider_copy_decoration_normalized_without_exposing_value(self):
        from portable_n8n import project_credentials
        import contextlib,io
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            project_credentials(self.api,self.root,'sample','cliFixture',['script'],lambda _: '  Bearer SYNTHETIC_PRIVATE_KEY\r\n')
        self.assertEqual(self.api.posts[0][1]['data']['value'],'Bearer SYNTHETIC_PRIVATE_KEY')
        self.assertNotIn('SYNTHETIC_PRIVATE_KEY',output.getvalue())
        self.assertNotIn('SYNTHETIC_PRIVATE_KEY',(self.root/'credentials-sample.json').read_text())
    def test_invalid_provider_input_retries_without_resubmitting_feishu(self):
        from portable_n8n import project_credentials
        import contextlib,io
        first=project_credentials(self.api,self.root,'sample','cliFixture',['feishu'],lambda _: 'SYNTHETIC_FEISHU')
        values=iter(['bad key','bad\x1bkey','SYNTHETIC_MODEL'])
        with contextlib.redirect_stdout(io.StringIO()):
            result=project_credentials(self.api,self.root,'sample','cliFixture',['feishu','script'],lambda _: next(values))
        self.assertEqual(result['feishu'],first['feishu']); self.assertEqual(len(self.api.posts),2)
        self.assertEqual(self.api.posts[-1][1]['data']['value'],'Bearer SYNTHETIC_MODEL')
    def test_provider_retry_limit_and_cancel_preserve_existing_checkpoint(self):
        from portable_n8n import project_credentials
        import contextlib,io
        project_credentials(self.api,self.root,'sample','cliFixture',['feishu'],lambda _: 'SYNTHETIC_FEISHU')
        checkpoint=(self.root/'credentials-sample.json').read_bytes(); prompts=[]
        def invalid(prompt): prompts.append(prompt); return 'bad key'
        with contextlib.redirect_stdout(io.StringIO()),self.assertRaisesRegex(Fault,'RAW_PROVIDER_KEY_REQUIRED'):
            project_credentials(self.api,self.root,'sample','cliFixture',['script'],invalid)
        self.assertEqual(len(prompts),3); self.assertEqual(len(self.api.posts),1)
        self.assertEqual((self.root/'credentials-sample.json').read_bytes(),checkpoint)
        self.assertFalse((self.root/'.provision.lock').exists())
        with self.assertRaisesRegex(Fault,'INPUT_CANCELLED'):
            project_credentials(self.api,self.root,'sample','cliFixture',['script'],lambda _: ':quit')
        self.assertEqual(len(self.api.posts),1)
    def test_unknown_project_credential_creation_never_reprompts_or_duplicates(self):
        from portable_n8n import project_credentials
        self.api.fail=True
        with self.assertRaises(Fault):
            project_credentials(self.api,self.root,'sample','cliFixture',['feishu'],lambda _: 'SYNTHETIC_SECRET')
        self.api.fail=False
        with self.assertRaisesRegex(Fault,'UNKNOWN'):
            project_credentials(self.api,self.root,'sample','cliFixture',['feishu'],lambda _: self.fail('must not ask again'))
        self.assertEqual(len(self.api.posts),1)
    def test_partial_native_install_resumes_without_erasing_data(self):
        import socket
        from portable_n8n import private_write
        with tempfile.TemporaryDirectory() as tools:
            node=Path(tools)/'node'
            npm=Path(tools)/'node_modules/npm/bin/npm-cli.js'
            npm.parent.mkdir(parents=True); npm.touch()
            calls=[]
            def run(command,cwd,**kwargs):
                if len(command)>1 and Path(command[1]).name=='npm-cli.js':
                    calls.append(kwargs)
                    if len(calls)==1:
                        private_write(self.root/'data/preserved.json',{'keep':True})
                        raise Fault('DEPENDENCY_INSTALL_TIMEOUT_PARTIAL_FILES_PRESERVED_RESUME_SAME_DIRECTORY')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
            with patch('portable_n8n.shutil.which',return_value=str(node)), patch('portable_n8n.subprocess.check_output',return_value='v24.22.0'), patch('portable_n8n.venv.EnvBuilder'), patch('portable_n8n.run_checked',side_effect=run):
                with self.assertRaisesRegex(Fault,'TIMEOUT'):
                    install_local(self.root,'node',port)
                self.assertFalse((self.root/'local.json').exists())
                self.assertFalse((self.root/'.provision.lock').exists())
                self.assertTrue(install_local(self.root,'node',port,dependency_timeout=2400)['installed'])
            self.assertEqual([c['timeout'] for c in calls],[1200,2400])
            self.assertEqual(json.loads((self.root/'data/preserved.json').read_text()),{'keep':True})
            self.assertEqual(calls[1]['env']['npm_config_cache'],str(self.root.resolve()/'npm-cache'))
    def test_invalid_install_deadline_never_mutates_target(self):
        for value in [0,59,7201,True]:
            with self.assertRaisesRegex(Fault,'TIMEOUT_MUST'):
                install_local(self.root,'node',dependency_timeout=value)
        self.assertEqual(list(self.root.iterdir()),[])
    def test_invalid_identifiers_rejected(self):
        with self.assertRaises(Fault): probe_workflow('a/../../b','table')

if __name__=='__main__': unittest.main()
