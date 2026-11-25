from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from people.models import Party


class PartyModelTests(TestCase):
    def test_shared_roles_optional_details_normalization_and_display(self):
        for index, roles in enumerate([
            {'is_customer': True}, {'is_supplier': True}, {'is_customer': True, 'is_supplier': True},
        ]):
            with self.subTest(roles=roles):
                party = Party(code=f'  party-{index}  ', name='  Example company  ', **roles)
                party.full_clean(exclude=['create_by', 'modified_by'])
                party.save()
                self.assertEqual(party.code, f'PARTY-{index}')
                self.assertEqual(party.name, 'Example company')
                self.assertEqual(party.email, '')
                self.assertTrue(party.is_active)
                self.assertEqual(str(party), f'PARTY-{index} — Example company')
                self.assertIsNotNone(party.created_at)
                self.assertIsNotNone(party.updated_at)

    def test_validation_rejects_missing_roles_whitespace_and_invalid_contacts(self):
        for changes in [{'is_supplier': False}, {'code': '   '}, {'name': '   '},
                        {'email': 'invalid'}, {'website': 'invalid'}, {'code': 'A' * 41}]:
            with self.subTest(changes=changes):
                party = Party(**{'code': 'SUP-1', 'name': 'Supplier', 'is_supplier': True, **changes})
                with self.assertRaises(ValidationError):
                    party.full_clean(exclude=['create_by', 'modified_by'])

    def test_database_protects_role_and_case_insensitive_code_constraints(self):
        Party.objects.create(code='SUP-1', name='Original', is_supplier=True)
        for code in ['SUP-1', 'sup-1', '  SuP-1  ']:
            with self.subTest(code=code):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Party.objects.create(code=code, name='Duplicate', is_customer=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Party.objects.create(code='NO-ROLE', name='Missing role')

    def test_ordering_is_stable_for_duplicate_names(self):
        first = Party.objects.create(code='B', name='Same', is_supplier=True)
        second = Party.objects.create(code='C', name='Same', is_customer=True)
        alphabetic_first = Party.objects.create(code='A', name='Alpha', is_customer=True)
        self.assertEqual(list(Party.objects.values_list('pk', flat=True)), [alphabetic_first.pk, first.pk, second.pk])
