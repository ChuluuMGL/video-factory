"""Execute only the two packaged reviewed sources on fixed synthetic cases.

This is a known-code guard, not a sandbox for arbitrary user Python. The parent
uses -I, a clean environment and a timeout; no user path or fixtures are accepted.
"""
import json
import resource
import sys

from .repair_profile import check


def main():
    raw = sys.stdin.buffer.read(32769)
    if len(raw) > 32768: raise ValueError('REPAIR_INPUT_TOO_LARGE')
    reviewed = check(json.loads(raw))
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))

    def no_io(event, args):
        if event == 'open' or event.startswith(('socket.', 'subprocess.', 'os.system', 'os.exec', 'os.spawn')):
            raise RuntimeError('REPAIR_EXTERNAL_IO_DISABLED')

    sys.addaudithook(no_io)
    scope = {}
    exec(compile(reviewed['text'], '<reviewed-isolated-decoder>', 'exec'), scope)
    cases = [('string', '失败', True), ('single-select', ['失败/需处理'], True),
             ('empty', [], False), ('multiple', ['失败', '失败'], False),
             ('nested', [['失败']], False), ('object', {'text': '失败'}, False),
             ('number', 1, False), ('unrelated', '已通过', False)]
    results = []
    for name, value, expected in cases:
        try: scope['decode_status'](value); accepted = True
        except ValueError: accepted = False
        results.append({'name': name, 'passed': accepted == expected})
    rows = [{'id': 'fixture-'+str(i), 'status': ['失败/需处理'],
             'provider_id': 'existing-'+str(i), 'review_version': 'review-'+str(i),
             'feedback': 'Synthetic feedback'} for i in range(10)]

    def page(items, revision=1, more=False): return {'rows': items, 'revision': revision, 'more': more}
    def run(pages):
        try: return scope['scan'](pages)
        except ValueError as error: return str(error)

    observed = run([page(rows)])
    preserved = observed == rows
    # String inputs isolate pagination guards from the intentionally broken decoder.
    strings = [{**row, 'status': '失败'} for row in rows]
    guards = [run([page(strings[:1], more=True), page(strings[1:2])]) == strings[:2],
              run([page(strings[:1], more=True), page(strings[1:2], revision=2)]) == 'REVISION_CHANGED',
              run([page(strings[:1], more=True), page(strings[:1])]) == 'DUPLICATE_RECORD',
              run([page([], more=True)]) == 'EMPTY_PAGE']
    print(json.dumps({'kind': 'reviewed-python-source', 'executed_source_sha256': reviewed['sha256'],
          'passed': all(x['passed'] for x in results) and preserved and all(guards),
          'cases': results, 'pagination_guards_passed': sum(guards), 'synthetic_records': 10,
          'existing_references_preserved': preserved, 'real_model_calls': 0, 'production_writes': 0}))


if __name__ == '__main__': main()
