from common.models import Status
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase

from ..models import Brand, Product, ProductCategory, ProductType, UnitOfMeasure


class ProductTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.unit = UnitOfMeasure.objects.create(name='Pieces', code='PCS')

    def product(self, **changes):
        data = {'sku': 'SKU-001', 'name': 'Example product', 'currency_id': 'BDT',
                'unit': self.unit, 'create_by': 'tester', 'modified_by': 'tester'}
        data.update(changes)
        return Product(**data)

    def test_product_can_be_validated_saved_and_classified(self):
        category = ProductCategory.objects.create(name='Electronics', code='ELEC')
        brand = Brand.objects.create(name='Example brand')
        product = self.product(category=category, brand=brand, sale_price=Decimal('1500.1234'),
                               attributes={'color': 'blue'}, status=Status.objects.get(code='active'))
        product.full_clean()
        product.save()
        product.refresh_from_db()
        self.assertEqual(product.sale_price, Decimal('1500.1234'))
        self.assertEqual(product.attributes, {'color': 'blue'})
        self.assertEqual(product.status.code, 'active')
        self.assertEqual(product.product_type.code, 'goods')
        self.assertIsNotNone(product.public_id)

    def test_sku_uniqueness_is_case_and_surrounding_whitespace_insensitive(self):
        self.product().save()
        with self.assertRaises(ValidationError):
            self.product(sku=' sku-001 ').full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.product(sku=' sku-001 ').save()

    def test_optional_barcodes_allow_blanks_but_not_duplicate_values(self):
        self.product().save()
        self.product(sku='SKU-002').save()
        self.product(sku='SKU-003', barcode='123456789').save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.product(sku='SKU-004', barcode='123456789').save()

    def test_database_protects_current_price_and_tax_fields(self):
        cases = [{'cost_price': -1}, {'sale_price': -1}, {'Whole_sale_price': -1},
                 {'minimum_sale_price': 10, 'sale_price': 5},
                 {'product_tax': Decimal('100.0001')}, {'product_tax': -1}]
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(ValidationError):
                    self.product(**changes).full_clean()
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.product(**changes).save()

    def test_lookup_tables_allow_new_types_and_statuses_without_model_changes(self):
        product_type = ProductType.objects.create(code='rental', name='Rental')
        status = Status.objects.create(code='awaiting-review', name='Awaiting review')
        product = self.product(product_type=product_type, status=status)
        product.full_clean()
        product.save()
        product.refresh_from_db()
        self.assertEqual(product.product_type, product_type)
        self.assertEqual(product.status, status)
        self.assertTrue(product_type.products.filter(pk=product.pk).exists())
        self.assertTrue(status.products.filter(pk=product.pk).exists())

    def test_invalid_identifiers_metadata_and_lookup_codes_are_rejected(self):
        cases = [{'sku': '   '}, {'name': '   '}, {'currency_id': 'bdt'}, {'currency_id': ''},
                 {'attributes': []}, {'product_type_id': 'unknown'}, {'status_id': 'unknown'},
                 {'unit_id': self.unit.pk + 999}, {'variant_of_id': 999}]
        for changes in cases:
            with self.subTest(changes=changes):
                product = self.product()
                for field, value in changes.items():
                    setattr(product, field, value)
                with self.assertRaises(ValidationError):
                    product.full_clean()

    def test_variants_cannot_be_nested_or_parent_themselves(self):
        base = self.product()
        base.save()
        variant = self.product(sku='SKU-BLUE', variant_of=base, attributes={'color': 'blue'})
        variant.full_clean()
        variant.save()
        with self.assertRaises(ValidationError):
            self.product(sku='SKU-CHILD', variant_of=variant).full_clean()
        base.variant_of = base
        with self.assertRaises(ValidationError):
            base.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            base.save()

    def test_variant_and_parent_product_types_must_match(self):
        base = self.product()
        base.save()
        service = ProductType.objects.get(code='service')
        with self.assertRaises(ValidationError):
            self.product(sku='SKU-SERVICE', variant_of=base, product_type=service).full_clean()
        self.product(sku='SKU-BLUE', variant_of=base).save()
        base.product_type = service
        with self.assertRaises(ValidationError):
            base.full_clean()

    def test_referenced_units_types_and_statuses_are_protected(self):
        product = self.product()
        product.save()
        for related in [self.unit, product.product_type, product.status]:
            with self.subTest(model=related.__class__.__name__):
                with self.assertRaises(ProtectedError):
                    related.delete()

    def test_admin_preserves_stable_codes_and_default_lookup_records(self):
        root = get_user_model().objects.create_superuser(username='root@example.com', password=None)
        request = RequestFactory().get('/admin/')
        request.user = root
        for model, code in [(ProductType, 'goods'), (Status, 'draft')]:
            model_admin = admin.site._registry[model]
            obj = model.objects.get(code=code)
            self.assertIn('code', model_admin.get_readonly_fields(request, obj))
            self.assertFalse(model_admin.has_delete_permission(request, obj))

    def test_admin_sets_common_audit_fields(self):
        user = get_user_model().objects.create_user(username='catalog-admin@example.com')
        request = RequestFactory().post('/admin/')
        request.user = user
        product = self.product()
        admin.site._registry[Product].save_model(request, product, None, False)
        self.assertEqual(product.create_by, user.username)
        self.assertEqual(product.modified_by, user.username)
