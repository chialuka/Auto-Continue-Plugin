import concurrent.futures
import copy
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from direct_scheduler import DirectStore, Worker, reset_time, DEFAULT_INSTRUCTION
from direct_host import Host

THREAD = '12345678-1234-4234-8234-123456789abc'
RECEIPT = '23456789-2345-4345-8345-23456789abcd'


def usage(primary=20, secondary=30, reset=200, weekly=500, allowed=True):
    return {'accountId': 'a', 'ordinaryUsageAllowed': allowed, 'rateLimitsByLimitId': {'codex': {
        'primary': {'usedPercent': primary, 'resetsAt': reset},
        'secondary': {'usedPercent': secondary, 'resetsAt': weekly}}}}


class FakeHost:
    def __init__(self):
        self.snapshot = usage()
        self.reads = 0
        self.sent = []
        self.callback = None
        self.error = False

    def usage(self):
        self.reads += 1
        if self.callback:
            self.callback()
        return copy.deepcopy(self.snapshot)

    def queue(self, thread, instruction):
        self.sent.append((thread, instruction))
        if self.error:
            raise TimeoutError('receipt lost after acceptance')
        return RECEIPT


class DirectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = DirectStore(self.tmp.name)
        self.host = FakeHost()

    def tearDown(self):
        self.tmp.cleanup()

    def enable(self):
        return self.store.enable(THREAD, host=self.host, now=100)

    def test_reset_selection_accounts_for_all_exhausted_windows(self):
        self.assertEqual(reset_time(usage(), 'codex', 100), 260)
        self.assertEqual(reset_time(usage(100, 100), 'codex', 100), 560)
        with self.assertRaises(ValueError):
            reset_time(usage(reset=None), 'codex', 100)

    def test_enable_is_persisted_and_idempotent_without_another_usage_read(self):
        job = self.enable()
        self.assertEqual(job['due'], 260)
        self.assertEqual(DirectStore(self.tmp.name).status(THREAD)['generation'], job['generation'])
        self.assertEqual(self.enable()['generation'], job['generation'])
        self.assertEqual(self.host.reads, 1)

    def test_later_activity_cannot_replace_immutable_instruction(self):
        self.enable()
        with self.assertRaises(ValueError):
            self.store.enable(THREAD, 'A different instruction', host=self.host, now=101)
        self.assertEqual(self.store.status(THREAD)['instruction'], DEFAULT_INSTRUCTION)

    def test_no_read_or_queue_before_due(self):
        self.enable()
        self.assertIsNone(Worker(self.store, self.host).tick(259))
        self.assertEqual(self.host.reads, 1)
        self.assertEqual(self.host.sent, [])

    def test_worker_after_restart_queues_exact_instruction_once(self):
        self.enable()
        restarted = DirectStore(self.tmp.name)
        restarted.recover(261)
        worker = Worker(restarted, self.host)
        self.assertEqual(worker.tick(261), 'queued')
        self.assertIsNone(worker.tick(262))
        self.assertEqual(self.host.sent, [(THREAD, DEFAULT_INSTRUCTION)])
        self.assertEqual(restarted.status(THREAD)['queue_id'], RECEIPT)
        self.assertEqual(self.host.reads, 2)

    def test_cancel_does_not_fetch_or_queue(self):
        self.enable();self.store.cancel(THREAD)
        Worker(self.store, self.host).tick(261)
        self.assertEqual(self.host.reads, 1)
        self.assertEqual(self.host.sent, [])

    def test_cancel_while_usage_is_in_flight_prevents_queue(self):
        self.enable()
        self.host.callback = lambda: self.store.cancel(THREAD)
        self.assertEqual(Worker(self.store, self.host).tick(261), 'cancelled')
        self.assertEqual(self.host.sent, [])

    def test_concurrent_workers_claim_only_once(self):
        self.enable()
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda _: Worker(DirectStore(self.tmp.name), self.host).tick(261), range(5)))
        self.assertEqual(len(self.host.sent), 1)

    def test_blocked_delivery_waits_for_latest_exhausted_reset(self):
        self.enable()
        self.host.snapshot = usage(100, 100, reset=300, weekly=800, allowed=False)
        worker = Worker(self.store, self.host)
        self.assertEqual(worker.tick(261), 'deferred')
        self.assertEqual(self.store.status(THREAD)['due'], 860)
        self.assertEqual(self.host.sent, [])
        self.host.snapshot = usage(reset=1200, weekly=1800)
        self.assertEqual(worker.tick(861), 'queued')

    def test_unknown_usage_has_bounded_retries(self):
        self.enable();self.host.snapshot = usage(allowed=False)
        worker = Worker(self.store, self.host)
        for now in (261, 322, 443, 684):
            worker.tick(now)
        self.assertEqual(self.store.status(THREAD)['status'], 'failed')
        self.assertEqual(self.host.sent, [])

    def test_account_change_fails_without_queueing(self):
        self.enable();self.host.snapshot['accountId'] = 'b'
        Worker(self.store, self.host).tick(261)
        self.assertEqual(self.store.status(THREAD)['last_error'], 'account_changed')
        self.assertEqual(self.host.sent, [])

    def test_ambiguous_queue_receipt_is_not_automatically_replayed(self):
        self.enable();self.host.error = True
        worker = Worker(self.store, self.host)
        self.assertEqual(worker.tick(261), 'uncertain')
        self.store.recover(300)
        self.assertIsNone(worker.tick(301))
        self.assertEqual(len(self.host.sent), 1)

    def test_crash_recovery_retries_check_but_never_replays_a_send(self):
        job = self.enable();self.store.claim(261);self.store.recover(262)
        self.assertEqual(self.store.status(THREAD)['status'], 'armed')
        self.store.claim(263);self.store.begin_send(job, 263);self.store.recover(264)
        self.assertEqual(self.store.status(THREAD)['status'], 'uncertain')

    def test_expiry_and_invalid_chat_id(self):
        self.enable();Worker(self.store, self.host).tick(100+9*86400)
        self.assertEqual(self.store.status(THREAD)['status'], 'expired')
        with self.assertRaises(ValueError):
            self.store.enable('../../unrelated', host=self.host, now=100)

    def test_queue_cli_preserves_message_as_a_single_literal_argument(self):
        text = 'Continue\n$(touch NEVER) `literal`'
        class Result:
            returncode = 0
            stdout = f'Queued message {RECEIPT} for thread {THREAD}.'
        with patch('direct_host.subprocess.run', return_value=Result()) as run:
            self.assertEqual(Host('/bin/codex-test').queue(THREAD, text), RECEIPT)
        self.assertEqual(run.call_args.args[0], ['/bin/codex-test','queue','--thread',THREAD,'--message',text])
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_malformed_usage_defers_instead_of_leaving_a_stuck_check(self):
        self.enable();self.host.snapshot = {'accountId':'a','ordinaryUsageAllowed':True,
                                          'rateLimitsByLimitId':{'codex':{'primary':'invalid'}}}
        self.assertEqual(Worker(self.store, self.host).tick(261), 'deferred')
        self.assertEqual(self.store.status(THREAD)['status'], 'armed')

    def test_direct_mcp_setup_fetches_usage_in_code_without_host_tool_roundtrips(self):
        from engine import Store
        from server import handle
        snapshot = usage(reset=time.time()+120, weekly=time.time()+500)
        with patch('direct_service.ensure_service', return_value={'loaded':True}), \
             patch('direct_host.Host.usage', return_value=snapshot) as read, \
             patch('direct_host.executable', return_value='/fixture/codex'):
            result = handle({'method':'tools/call','params':{'name':'resume_direct_enable',
                      'arguments':{'thread':THREAD,'instruction':'Resume the interrupted task'}}}, Store(self.tmp.name))
        self.assertEqual(read.call_count,1)
        self.assertEqual(set(result['structuredContent']['job']), {'status','due','scheduledLocal','timezone'})
        self.assertEqual(result['structuredContent']['service'], {'loaded':True})
        self.assertEqual(result['structuredContent']['job']['due'], int(snapshot['rateLimitsByLimitId']['codex']['primary']['resetsAt']+1)+60)

    def test_status_formats_saved_timestamp_in_client_timezone_without_usage(self):
        from engine import Store
        from server import handle
        self.enable()
        with self.store.connect() as c:
            c.execute('UPDATE direct_jobs SET due=? WHERE thread=?', (1792381536, THREAD))
        with patch('direct_service.service_status', return_value={'loaded':True}), \
             patch('direct_host.Host.usage') as read:
            result = handle({'method':'tools/call','params':{'name':'resume_direct_status',
                     'arguments':{'thread':THREAD,'timezone':'Africa/Lagos'}}}, Store(self.tmp.name))
        job = result['structuredContent']['job']
        self.assertEqual(job['scheduledLocal'], '2026-10-19T04:45:36+01:00')
        self.assertEqual(job['scheduledUTC'], '2026-10-19T03:45:36+00:00')
        read.assert_not_called()
        self.assertEqual(self.store.status(THREAD)['due'], 1792381536)


if __name__ == '__main__':
    unittest.main()
