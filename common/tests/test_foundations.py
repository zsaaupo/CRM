from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase

from common.audit import log_audit_event
from common.exceptions import IdempotencyConflict
from common.idempotency import IdempotentResult, canonical_payload_hash, run_idempotent
from common.models import AuditLog, GeneralSetting, IdempotencyRecord
from common.money import allocate_pro_rata, convert_to_base, q_cost, q_money, q_qty, q_total_cost
from common.settings_store import GENERAL_SETTING_CACHE_KEY, get_general
from warehouse.models import WareHouse


class GeneralSettingTests(TestCase):
    def setUp(self):
        cache.delete(GENERAL_SETTING_CACHE_KEY)

    def test_singleton_and_cache_invalidation(self):
        first = get_general()
        self.assertEqual(first.pk, 1)
        self.assertEqual(first.costing_method, 'wavg')
        self.assertFalse(first.allow_sale_without_stock)
        self.assertEqual(first.decimal_digits, 2)

        with self.assertNumQueries(0):
            cached = get_general()
        self.assertEqual(cached.system_title, first.system_title)

        first.company_name = 'Updated ERP'
        first.save()
        self.assertIsNone(cache.get(GENERAL_SETTING_CACHE_KEY))
        refreshed = get_general()
        self.assertEqual(refreshed.company_name, 'Updated ERP')

        # Second save cannot create pk=2
        second = GeneralSetting(company_name='Another')
        second.save()
        self.assertEqual(GeneralSetting.objects.count(), 1)
        self.assertEqual(GeneralSetting.objects.get(pk=1).company_name, 'Another')

        with self.assertRaises(ValueError):
            second.delete()


class IdempotencyTests(TestCase):
    def test_idempotent_replay_and_conflict(self):
        key = uuid4()
        calls = 0

        def work():
            nonlocal calls
            calls += 1
            return IdempotentResult('test_doc', 42, {'status': 'ok', 'amount': '100.00'})

        res1, created1 = run_idempotent(key, 'sales_payment', {'amount': Decimal('100.00')}, work)
        self.assertTrue(created1)
        self.assertEqual(calls, 1)
        self.assertEqual(res1['result_id'] if 'result_id' in res1 else res1['status'], 'ok')

        # Equivalent Decimal normalization + key order replays without calling fn
        res2, created2 = run_idempotent(str(key), 'sales_payment', {'amount': Decimal('100')}, work)
        self.assertFalse(created2)
        self.assertEqual(calls, 1)
        self.assertEqual(res2, res1)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

        # Different payload -> 409 IdempotencyConflict
        with self.assertRaises(IdempotencyConflict):
            run_idempotent(key, 'sales_payment', {'amount': Decimal('200.00')}, work)

        # Different scope -> 409 IdempotencyConflict
        with self.assertRaises(IdempotencyConflict):
            run_idempotent(key, 'purchase_payment', {'amount': Decimal('100.00')}, work)

    def test_canonical_payload_hash_stable(self):
        h1 = canonical_payload_hash({'b': [1, 2], 'a': Decimal('10.50')})
        h2 = canonical_payload_hash({'a': Decimal('10.5'), 'b': [1, 2]})
        self.assertEqual(h1, h2)


class AuditLogTests(TestCase):
    def test_append_only_redaction_and_dedup(self):
        user = get_user_model().objects.create_user(username='auditor')
        wh = WareHouse.objects.create(name='Main', address='Dhaka', phone='017')
        req = RequestFactory().post('/test/', HTTP_USER_AGENT='TestAgent/1.0', REMOTE_ADDR='127.0.0.1')
        req_key = uuid4()

        entry = log_audit_event(
            user,
            AuditLog.Action.SETTINGS_CHANGE,
            instance=wh,
            changes={
                'name': {'old': 'A', 'new': 'B'},
                'api_key': {'old': 'secret1', 'new': 'secret2'},
                'password': {'old': 'p1', 'new': 'p2'},
            },
            request=req,
            module=AuditLog.Module.WAREHOUSE,
            request_key=req_key,
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry.warehouse_id, wh.pk)
        self.assertEqual(entry.ip_address, '127.0.0.1')
        self.assertEqual(entry.user_agent, 'TestAgent/1.0')
        self.assertEqual(entry.changes['api_key'], {'old': '[REDACTED]', 'new': '[REDACTED]'})
        self.assertEqual(entry.changes['password'], {'old': '[REDACTED]', 'new': '[REDACTED]'})

        # Duplicate request_key + action + model + object_id is a no-op
        dup = log_audit_event(
            user,
            AuditLog.Action.SETTINGS_CHANGE,
            instance=wh,
            changes={'name': {'old': 'A', 'new': 'B'}},
            request=req,
            module=AuditLog.Module.WAREHOUSE,
            request_key=req_key,
        )
        self.assertIsNone(dup)
        self.assertEqual(AuditLog.objects.count(), 1)

        with self.assertRaises(ValueError):
            entry.object_repr = 'tamper'
            entry.save()
        with self.assertRaises(ValueError):
            entry.delete()
        with self.assertRaises(ValueError):
            AuditLog.objects.update(object_repr='tamper')
        with self.assertRaises(ValueError):
            AuditLog.objects.delete()


class MoneyUtilsTests(TestCase):
    def test_quantizers_and_pro_rata_residual(self):
        self.assertEqual(q_money('10.125'), Decimal('10.13'))
        self.assertEqual(q_money('10.125', digits=4), Decimal('10.1250'))
        self.assertEqual(q_qty('1.1234565'), Decimal('1.123457'))
        self.assertEqual(q_cost('5.0000005'), Decimal('5.000001'))
        self.assertEqual(q_total_cost('99.99995'), Decimal('100.0000'))
        self.assertEqual(convert_to_base('10', '120.5'), Decimal('1205.0000'))

        shares = allocate_pro_rata(Decimal('10.00'), [Decimal('1'), Decimal('1'), Decimal('1')], digits=2)
        self.assertEqual(shares, [Decimal('3.33'), Decimal('3.33'), Decimal('3.34')])
        self.assertEqual(sum(shares), Decimal('10.00'))

        # Residual lands on last positive-weight line even if trailing weight is 0
        shares2 = allocate_pro_rata(Decimal('10.00'), [Decimal('1'), Decimal('2'), Decimal('0')], digits=2)
        self.assertEqual(shares2, [Decimal('3.33'), Decimal('6.67'), Decimal('0.00')])
        self.assertEqual(sum(shares2), Decimal('10.00'))
