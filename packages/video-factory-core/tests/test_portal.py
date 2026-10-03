import http.client
import json
import tempfile
import unittest

from portal_fixture import PortalFixture
from video_factory.feishu_bridge import meta, save
from video_factory.portal_http import PortalDirectory, project_manifest
from video_factory.runtime_store import RuntimeFault


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.now=100
        self.f=PortalFixture(self.temp.name,clock=lambda:self.now)
        self.cookie='';self.csrf=''
        status,value,headers=self.request('/api/session')
        self.assertEqual(status,200);self.csrf=value['csrf'];self.cookie=headers['Set-Cookie'].split(';')[0]

    def tearDown(self): self.f.close();self.temp.cleanup()

    def request(self,path,body=None,extra=None):
        client=http.client.HTTPConnection('127.0.0.1',self.f.server.server_port,timeout=5)
        headers={'Host':'portal.example','Cookie':self.cookie}
        if body is not None: headers.update({'Origin':'https://portal.example','Content-Type':'application/json','X-VF-CSRF':self.csrf})
        headers.update(extra or {})
        client.request('GET' if body is None else 'POST',path,None if body is None else json.dumps(body),headers)
        response=client.getresponse();raw=response.read()
        value=json.loads(raw) if 'application/json' in response.getheader('Content-Type','') else raw
        result=response.status,value,dict(response.getheaders());client.close();return result

    def login(self):
        self.assertEqual(self.request('/api/login',{})[0],200)
        self.f.f.granted=True;self.now+=5
        self.assertEqual(self.request('/api/poll',{})[1]['status'],'authorized')

    def prepare(self,project):
        return self.request('/p/'+project+'/api/review/prepare',{'task':'task_one','revision':1,'stage':'script','decision':'reject','feedback':'Revise this project only'})

    def test_unauthenticated_directory_and_unscoped_routes_are_closed(self):
        for path in ('/api/projects','/p/alpha/api/tasks','/p/beta/api/tasks'):
            self.assertEqual(self.request(path)[0],401)
        for path in ('/api/session','/healthz'):
            raw=json.dumps(self.request(path)[1]);self.assertNotIn('alpha',raw);self.assertNotIn('beta',raw)
        self.login()
        for path in ('/api/tasks','/p/unknown/api/tasks','/p/alpha/api/session','/p/%61lpha/api/tasks'):
            self.assertNotEqual(self.request(path)[0],200)
        self.assertEqual(self.request('/p/alpha/api/tasks',extra={'Host':'evil.example'})[0],409)
        self.assertEqual(self.request('/p/alpha/api/task',{'task':'task_one','revision':1}, {'Origin':'https://evil.example'})[0],409)

    def test_identical_task_ids_keep_separate_data_and_pending_plans(self):
        self.login()
        self.assertEqual([x['id'] for x in self.request('/api/projects')[1]['items']],['alpha','beta'])
        body={'task':'task_one','revision':1}
        a=self.request('/p/alpha/api/task',body)[1];b=self.request('/p/beta/api/task',body)[1]
        self.assertNotEqual(a['input']['script'],b['input']['script'])
        status,plan,_=self.prepare('alpha');self.assertEqual(status,200)
        self.assertEqual(self.request('/p/beta/api/commit',{'plan_sha256':plan['plan_sha256']})[1]['error'],'REVIEW_PLAN_REQUIRED')
        # A second tab reading another project does not replace the first tab's scope.
        self.request('/p/beta/api/tasks')
        self.assertEqual(self.request('/p/alpha/api/commit',{'plan_sha256':plan['plan_sha256']})[1]['receipt']['state'],'rejected')
        self.assertEqual(self.request('/p/beta/api/task',body)[1]['state'],'awaiting_script_review')

    def test_revocation_hides_project_and_denies_prepared_commit(self):
        self.login();_,plan,_=self.prepare('alpha');self.f.revoke('alpha')
        self.assertEqual([x['id'] for x in self.request('/api/projects')[1]['items']],['beta'])
        self.assertEqual(self.request('/p/alpha/api/commit',{'plan_sha256':plan['plan_sha256']})[0],409)
        self.assertEqual(self.request('/p/alpha/api/tasks')[0],409)
        self.f.revoke('beta');self.assertEqual(self.request('/api/projects')[1]['items'],[])

    def test_media_hash_is_scoped_and_rechecked_after_role_change(self):
        a=self.f.media('alpha',b'alpha-media');b=self.f.media('beta',b'beta-media');self.login()
        url=self.request('/p/alpha/api/task',{'task':'task_one','revision':1})[1]['media_url']
        self.assertEqual(url,'/p/alpha/media/task_one/1/'+a+'.mp4')
        self.assertEqual(self.request(url)[1],b'alpha-media')
        self.assertEqual(self.request(url,extra={'Range':'bytes=0-4'})[1],b'alpha')
        self.assertEqual(self.request(url.replace('/alpha/','/beta/'))[0],409)
        self.assertEqual(self.request('/media/task_one/1/'+a+'.mp4')[0],409)
        self.f.revoke('alpha');self.assertEqual(self.request(url)[0],409)
        self.assertEqual(self.request('/p/beta/media/task_one/1/'+b+'.mp4')[0],200)

    def test_identity_and_profile_changes_fail_closed(self):
        self.login();self.f.f.identity['tenant_key']='other_tenant'
        self.assertEqual(self.request('/api/projects')[1]['items'],[])
        self.assertEqual(self.request('/p/beta/api/tasks')[0],409)
        self.f.f.identity['tenant_key']='fixture_tenant'
        with self.f.store.connect() as db:
            profile=meta(db,'setup:feishu-app:beta');profile['app_id']='cli_other';save(db,'setup:feishu-app:beta',profile)
        self.assertEqual(self.request('/p/beta/api/tasks')[0],409)
        self.assertEqual([x['id'] for x in self.request('/api/projects')[1]['items']],['alpha'])
        with self.assertRaisesRegex(RuntimeFault,'SHARED_FEISHU_CONTEXT'):
            PortalDirectory(self.f.store,['alpha','beta'],self.f.f.media,client_factory=self.f.f.bridge.client_factory)

    def test_login_can_start_with_only_secondary_membership_and_logout_clears_plans(self):
        self.f.revoke('alpha');self.login()
        self.assertEqual([x['id'] for x in self.request('/api/projects')[1]['items']],['beta'])
        self.request('/p/beta/api/import/prepare',{'record':'recFixture','expected_revision':1})
        self.assertEqual(self.request('/api/logout',{})[0],200)
        self.assertNotIn('projects',next(iter(self.f.server.sessions.values())))
        self.assertEqual(self.request('/p/beta/api/tasks')[0],401)
        self.login();self.now+=28801
        self.assertEqual(self.request('/api/projects')[0],401)

    def test_manifest_is_explicit_bounded_unique_and_validated(self):
        for projects in ([],['alpha','alpha'],['../alpha'],['a']*21):
            with self.assertRaises(RuntimeFault):project_manifest(projects)
        self.assertEqual(project_manifest(['beta','alpha']),['alpha','beta'])
