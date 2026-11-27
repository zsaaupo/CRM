"""Read consistent, permission-scoped snapshots without performing business actions."""
from decimal import Decimal
from textwrap import wrap

from django.conf import settings
from django.db import transaction
from django.db.models import Prefetch, prefetch_related_objects
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.exceptions import NotFound, PermissionDenied

from account.permissions import has_role_permissions
from sale.models import Chalan
from warehouse.scope import scoped_queryset, warehouse_scope
from warehouse.services import transfer_queryset
from .catalog import REPORTS


def money(value):
    return f'{Decimal(value):,.4f}'


def quantity(value):
    return f'{Decimal(value):,.6f}'.rstrip('0').rstrip('.')


def date_label(value):
    return value.strftime('%d %b %Y') if value else 'Not specified'


def address_lines(record):
    if record is None:
        return []
    return [str(value) for value in [getattr(record, 'address', ''),
        ', '.join(filter(None, [getattr(record, 'city', ''), getattr(record, 'state', ''), getattr(record, 'postal_code', '')])),
        getattr(record, 'country', ''), getattr(record, 'phone', ''), getattr(record, 'email', '')] if value]


def company_branding():
    company = {'name': 'eManager', 'address': '', 'phone': '', 'email': '', 'logo_path': ''}
    company.update(getattr(settings, 'REPORT_COMPANY', {}))
    return company


def total_rows(amounts, shipping_cost):
    subtotal, tax, grand_total = amounts
    return [('Subtotal after discounts', money(subtotal)), ('Tax', money(tax)),
            ('Shipping', money(shipping_cost)), ('Grand total', money(grand_total))]


def product_row(item, *, financial):
    row = {'number': item.line_number, 'sku': item.product.sku, 'name': item.product.name,
           'unit': item.unit.code, 'quantity': quantity(item.quantity)}
    if financial:
        _, tax, total = item.calculate_amounts()
        row.update({'price': money(item.unit_price), 'discount': money(item.discount_amount),
                    'tax': money(tax), 'tax_rate': format(item.tax_rate.normalize(), 'f') if item.tax_rate else '0',
                    'total': money(total), 'description_parts': wrap(item.description, width=650,
                        replace_whitespace=False, drop_whitespace=False)})
    return row


def snapshot(kind, pk, actor):
    spec = REPORTS[kind]
    with transaction.atomic():
        if not has_role_permissions(actor, spec.permissions):
            raise PermissionDenied('Your role does not have permission to download this document.')
        scope = warehouse_scope(actor)
        if kind == 'packing-slip':
            records = scoped_queryset(Chalan.objects.filter(packing_slip__pk=pk), scope)
            chalan = get_object_or_404(records.select_related('packing_slip', 'customer', 'warehouse')
                                      .select_for_update(of=('self',)))
            return document_context(kind, chalan.packing_slip)
        records = spec.model.objects.all()
        records = transfer_queryset(records, scope) if kind == 'transfer' else scoped_queryset(records, scope)
        # Lock the header without nullable related joins (required for PostgreSQL).
        record = get_object_or_404(records.select_for_update(), pk=pk)
        if kind == 'quotation':
            if not record.issued_document:
                raise NotFound('Issued quotation PDF not found.')
            return record.issued_document
        relations = ['source_warehouse', 'destination_warehouse'] if kind == 'transfer' else [spec.party_field, 'warehouse', 'currency', 'status']
        item_model = spec.model._meta.get_field('items').related_model
        prefetch_related_objects([record], *relations,
            Prefetch('items', queryset=item_model.objects.select_related('product', 'unit').order_by('line_number', 'pk')))
        return document_context(kind, record)


def document_context(kind, record, *, items=None):
    spec = REPORTS[kind]
    if kind == 'packing-slip':
        return packing_slip_context(spec, record, items)
    if items is None:
        items = list(record.items.all())
    completed = bool(getattr(record, spec.completed_field))
    number = record.transfer_number if kind == 'transfer' else (
        record.quotation_number if kind == 'quotation' else record.order_number)
    notes = record.terms if kind == 'quotation' else record.notes
    document = {
        'kind': kind, 'title': spec.title, 'number': number, 'company': company_branding(),
        'filename': f'{kind}-{slugify(number)}.pdf', 'completed': completed,
        'state': spec.completed_label if completed else spec.pending_label,
        'date': date_label(record.transfer_date if kind == 'transfer' else record.order_date),
        'generated_at': timezone.localtime().strftime('%d %b %Y, %H:%M %Z'),
        'prepared_by': record.create_by or 'Not recorded', 'notes': notes,
        'notes_label': 'Terms' if kind == 'quotation' else 'Notes', 'notes_compact': len(notes) <= 600,
        'line_count': len(items), 'financial': kind != 'transfer', 'rows': [],
        'metadata': [], 'cards': [], 'totals': [],
    }
    if kind == 'transfer':
        for label, warehouse in [('Source warehouse', record.source_warehouse), ('Destination warehouse', record.destination_warehouse)]:
            document['cards'].append({'label': label, 'name': warehouse.name,
                'code': warehouse.code or '', 'lines': address_lines(warehouse)})
    else:
        party = getattr(record, spec.party_field)
        document['currency'] = record.currency_id
        document['metadata'] = [
            ('Expected date', date_label(getattr(record, spec.expected_field))),
            ('Valid until', date_label(record.valid_until)) if kind == 'quotation' else ('Order status', record.status.name),
            ('Currency', record.currency_id),
            ('Exchange rate', f'{record.exchange_rate:.8f} {settings.INVENTORY_BASE_CURRENCY} per {record.currency_id}'),
        ]
        document['cards'].append({'label': spec.party_field.capitalize(),
            'name': party.name if party else 'Not specified', 'code': party.code if party else '',
            'lines': address_lines(party)})
        warehouse = record.warehouse
        document['cards'].append({'label': 'Warehouse', 'name': warehouse.name if warehouse else 'Not specified',
            'code': warehouse.code if warehouse and warehouse.code else '', 'lines': address_lines(warehouse)})
        reference = record.supplier_reference if kind == 'purchase' else record.customer_reference
        if reference:
            document['metadata'].append(('Supplier reference' if kind == 'purchase' else 'Customer reference', reference))
        if kind == 'quotation':
            document['metadata'].append(('Prepared by', document['prepared_by']))
        if kind in ('sale', 'quotation'):
            document['addresses'] = [('Billing address', record.billing_address), ('Shipping address', record.shipping_address)]
        document['totals'] = total_rows(record.calculate_totals(), record.shipping_cost)
    document['rows'] = [product_row(item, financial=document['financial']) for item in items]
    return document


def packing_slip_context(spec, slip, items):
    chalan = slip.chalan
    if items is None:
        items = list(chalan.items.select_related('product', 'unit'))
    metadata = [('Chalan number', chalan.chalan_number), ('Chalan date', date_label(chalan.chalan_date))]
    if chalan.expected_delivery_date:
        metadata.append(('Expected delivery', date_label(chalan.expected_delivery_date)))
    if chalan.customer_reference:
        metadata.append(('Customer reference', chalan.customer_reference))
    if chalan.currency_id != settings.INVENTORY_BASE_CURRENCY:
        metadata.append(('Exchange rate', f'{chalan.exchange_rate:.8f} {settings.INVENTORY_BASE_CURRENCY} per {chalan.currency_id}'))
    return {
        'kind': spec.kind, 'title': spec.title, 'number': slip.slip_number, 'company': company_branding(),
        'filename': f'packing-slip-{slugify(slip.slip_number)}.pdf',
        'date': date_label(timezone.localdate(slip.created_at)),
        'generated_at': timezone.localtime().strftime('%d %b %Y, %H:%M %Z'),
        'financial': True, 'currency': chalan.currency_id, 'line_count': len(items),
        'metadata': metadata,
        'cards': [
            {'label': 'Ship to', 'name': chalan.recipient_name or chalan.customer.name,
             'lines': [value for value in (chalan.customer.name if chalan.recipient_name else '',
                                           chalan.recipient_phone, chalan.shipping_address) if value]},
            {'label': 'Warehouse', 'name': chalan.warehouse.name, 'code': chalan.warehouse.code},
        ],
        'rows': [product_row(item, financial=True) for item in items],
        'totals': total_rows(chalan.calculate_totals(items=items), chalan.shipping_cost),
    }
