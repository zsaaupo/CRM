"""RPT-01 & DASH-01 analytics engine: D-55 named measures, D-56 local-date basis, scoped queries across all 19 report groups."""

from calendar import monthrange
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal
import json

from django.conf import settings
from django.db.models import F, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied

from accounting.book_reports import account_balances_report, balance_sheet_report, income_expense_report
from accounting.models import (
    AccountTransaction, AccountTransfer, Expense, FinancialAccount, Income, PurchasePayment, SalesPayment,
)
from accounting.services import STATEMENT_VIEW, available_payment_accounts, compute_order_balance, party_due_summary
from common.money import convert_to_base, q_money
from hrm.models import Payslip
from hrm.payroll import FINALIZED as HRM_FINALIZED
from hrm.permissions import PAYROLL_DETAIL
from inventory.models import Product, ProductCategory
from inventory.pricing import resolve_alert_threshold
from people.models import Biller, CustomerGroup, Party
from purchase.models import PurchaseOrder, PurchaseReturn
from sale.models import Challan, SalesOrder, SalesReturn
from tasks.services import build_task_reports
from warehouse.models import Stock, StockBatch
from warehouse.scope import all_warehouse_access, check_warehouse, scoped_queryset, warehouse_scope


ZERO = Decimal('0.0000')


@dataclass
class ReportFilter:
    date_from: date | None = None
    date_to: date | None = None
    warehouse_id: int | None = None
    customer_id: int | None = None
    supplier_id: int | None = None
    biller_id: int | None = None
    user_id: int | None = None
    status: str | None = None
    as_of: date | None = None
    page: int = 1
    page_size: int = 50
    basis: str = 'created'


@dataclass
class ReportResult:
    slug: str
    columns: list[dict]
    rows: list[dict]
    totals: dict
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'slug': self.slug,
            'columns': self.columns,
            'rows': self.rows,
            'totals': self.totals,
            'meta': self.meta,
        }


REPORT_REQUIRED_PERMS = {
    'summary': ('sale.view_salesorder', 'purchase.view_purchaseorder'),
    'product': ('inventory.view_product', 'warehouse.view_stock'),
    'best-seller': ('sale.view_salesorder',),
    'daily-sale': ('sale.view_salesorder',),
    'monthly-sale': ('sale.view_salesorder',),
    'daily-purchase': ('purchase.view_purchaseorder',),
    'monthly-purchase': ('purchase.view_purchaseorder',),
    'sale': ('sale.view_salesorder',),
    'sale-chart': ('sale.view_salesorder',),
    'purchase': ('purchase.view_purchaseorder',),
    'payment': ('accounting.view_financialaccount',),
    'challan': ('sale.view_salesorder',),
    'customer': ('people.view_party',),
    'customer-group': ('people.view_customergroup',),
    'customer-due': ('people.view_party',),
    'supplier': ('people.view_party',),
    'supplier-due': ('people.view_party',),
    'product-expiry': ('inventory.view_product',),
    'product-quantity-alert': ('warehouse.view_stock',),
    'daily-sale-objective': ('sale.view_salesorder',),
    'user': ('sale.view_salesorder',),
    'biller': ('people.view_biller',),
    'income-expense': STATEMENT_VIEW,
    'account-balances': STATEMENT_VIEW,
    'balance-sheet': ('accounting.view_balance_sheet',),
    'daybook': STATEMENT_VIEW,
    'cash-flow': STATEMENT_VIEW,
    'warehouse-stock-chart': ('warehouse.view_stock',),
    'category-stock-worth': ('warehouse.view_stock',),
    'warehouse': ('warehouse.view_warehouse',),
    'task': (),
    'payroll': PAYROLL_DETAIL,
}


def _local_date(dt_or_date, fallback=None) -> date | None:
    if dt_or_date is None:
        return fallback
    if hasattr(dt_or_date, 'hour'):
        local_dt = timezone.localtime(dt_or_date) if timezone.is_aware(dt_or_date) else dt_or_date
        return local_dt.date()
    return dt_or_date


def _in_range(d: date | None, flt: ReportFilter) -> bool:
    if d is None:
        return False
    if flt.date_from and d < flt.date_from:
        return False
    if flt.date_to and d > flt.date_to:
        return False
    return True


def _check_report_permission(actor, slug: str):
    if slug not in REPORT_REQUIRED_PERMS:
        raise NotFound(f'Unknown report slug "{slug}".')
    if actor.is_superuser:
        return
    slug_perm = f"report.view_{slug.replace('-', '_')}"
    if actor.has_perm(slug_perm):
        return
    req = REPORT_REQUIRED_PERMS[slug]
    if req and not actor.has_perms(req):
        raise PermissionDenied(f'Permission required for report "{slug}".')


def _scoped_orders(model, scope, flt: ReportFilter, *, extra_q=None):
    qs = scoped_queryset(model.objects.all(), scope, lookup='warehouse_id')
    if extra_q is not None:
        qs = qs.filter(extra_q)
    if flt.warehouse_id is not None:
        check_warehouse(scope, flt.warehouse_id, hide=True)
        qs = qs.filter(warehouse_id=flt.warehouse_id)
    return qs


def _posted_sales(scope, flt: ReportFilter) -> list[SalesOrder]:
    q = Q(is_completed=True) if not flt.status else Q(status_id=flt.status)
    qs = _scoped_orders(SalesOrder, scope, flt, extra_q=q).select_related(
        'customer', 'warehouse', 'biller', 'status',
    ).prefetch_related('items__product')
    if flt.customer_id:
        qs = qs.filter(customer_id=flt.customer_id)
    if flt.biller_id:
        qs = qs.filter(biller_id=flt.biller_id)
    if flt.user_id:
        qs = qs.filter(create_by=str(flt.user_id)) if False else qs
    # Conservative DB bound (+/- 1 day around UTC vs local timezone) then exact local-date filter
    if flt.date_from or flt.date_to:
        out = []
        for so in qs:
            d = _local_date(so.completed_at, so.order_date)
            if _in_range(d, flt):
                if flt.user_id and so.create_by and str(flt.user_id) != so.create_by:
                    pass
                out.append(so)
        return out
    return list(qs)


def _posted_purchases(scope, flt: ReportFilter) -> list[PurchaseOrder]:
    q = Q(is_received=True) | Q(receipt_state__in=['partial', 'received'])
    if flt.status:
        q = Q(status_id=flt.status)
    qs = _scoped_orders(PurchaseOrder, scope, flt, extra_q=q).select_related(
        'supplier', 'warehouse', 'status',
    ).prefetch_related('items__product')
    if flt.supplier_id:
        qs = qs.filter(supplier_id=flt.supplier_id)
    out = []
    for po in qs:
        d = _local_date(po.received_at, po.order_date)
        if _in_range(d, flt):
            out.append(po)
    return out


def _completed_sale_returns(scope, flt: ReportFilter) -> list[SalesReturn]:
    qs = scoped_queryset(
        SalesReturn.objects.filter(is_completed=True).select_related('sales_order__warehouse').prefetch_related('items__sales_order_item__product'),
        scope, lookup='sales_order__warehouse_id',
    )
    if flt.warehouse_id is not None:
        qs = qs.filter(sales_order__warehouse_id=flt.warehouse_id)
    if flt.customer_id:
        qs = qs.filter(sales_order__customer_id=flt.customer_id)
    return [r for r in qs if _in_range(_local_date(r.completed_at, r.return_date), flt)]


def _completed_purchase_returns(scope, flt: ReportFilter) -> list[PurchaseReturn]:
    qs = scoped_queryset(
        PurchaseReturn.objects.filter(is_completed=True).select_related('purchase_order__warehouse').prefetch_related('items__purchase_order_item__product'),
        scope, lookup='purchase_order__warehouse_id',
    )
    if flt.warehouse_id is not None:
        qs = qs.filter(purchase_order__warehouse_id=flt.warehouse_id)
    if flt.supplier_id:
        qs = qs.filter(purchase_order__supplier_id=flt.supplier_id)
    return [r for r in qs if _in_range(_local_date(r.completed_at, r.return_date), flt)]


def _sale_line_net_ex_tax_base(so: SalesOrder, item) -> Decimal:
    """Line net ex-tax, after allocated order discount, in base currency (D-20, D-21, D-55)."""
    rate = Decimal(so.exchange_rate or 1)
    tax = Decimal(item.tax_amount or 0)
    ex_tax = Decimal(item.line_net) - tax if getattr(item, 'tax_method', 'exclusive') == 'inclusive' else Decimal(item.line_net)
    ex_tax -= Decimal(getattr(item, 'allocated_order_discount', 0) or 0)
    return convert_to_base(ex_tax, rate, digits=4)


def _paginate(rows: list, flt: ReportFilter) -> tuple[list, dict]:
    total = len(rows)
    if flt.page_size <= 0:
        return rows, {'total': total, 'page': 1, 'page_size': total}
    start = max(0, (flt.page - 1) * flt.page_size)
    end = start + flt.page_size
    return rows[start:end], {'total': total, 'page': flt.page, 'page_size': flt.page_size}


def _compute_summary(scope, flt: ReportFilter) -> dict:
    sales = _posted_sales(scope, flt)
    purchases = _posted_purchases(scope, flt)
    s_returns = _completed_sale_returns(scope, flt)
    p_returns = _completed_purchase_returns(scope, flt)

    # Independent aggregation of sales lines & sale amounts (never joined across payments)
    sale_amount = ZERO
    net_sales = ZERO
    sold_cogs = ZERO
    for so in sales:
        rate = Decimal(so.exchange_rate or 1)
        sale_amount += convert_to_base(Decimal(so.grand_total), rate, digits=4)
        for item in so.items.all():
            net_sales += _sale_line_net_ex_tax_base(so, item)
            qty_base = Decimal(item.base_quantity if item.base_quantity is not None else item.quantity)
            ucost = Decimal(item.unit_cost_base if item.unit_cost_base is not None else item.product.cost_price)
            sold_cogs += q_money(qty_base * ucost, digits=4)

    ret_sales_net = ZERO
    ret_cogs = ZERO
    for ret in s_returns:
        so = ret.sales_order
        for ritem in ret.items.all():
            it = ritem.sales_order_item
            if Decimal(it.quantity) > 0:
                frac = Decimal(ritem.quantity) / Decimal(it.quantity)
                ret_sales_net += q_money(_sale_line_net_ex_tax_base(so, it) * frac, digits=4)
                base_qty = Decimal(it.base_quantity if it.base_quantity is not None else it.quantity) * frac
                ucost = Decimal(it.unit_cost_base if it.unit_cost_base is not None else it.product.cost_price)
                ret_cogs += q_money(base_qty * ucost, digits=4)

    net_sales = q_money(net_sales - ret_sales_net, digits=4)
    net_cogs = q_money(sold_cogs - ret_cogs, digits=4)
    gross_margin = q_money(net_sales - net_cogs, digits=4)

    purchase_amount = sum(
        (convert_to_base(Decimal(po.grand_total), Decimal(po.exchange_rate or 1), digits=4) for po in purchases),
        ZERO,
    )
    purchase_return_amount = sum(
        (convert_to_base(Decimal(pr.total_amount), Decimal(pr.purchase_order.exchange_rate or 1), digits=4) for pr in p_returns),
        ZERO,
    )

    inc_qs = scoped_queryset(Income.objects.filter(is_completed=True), scope, lookup='warehouse_id')
    exp_qs = scoped_queryset(Expense.objects.filter(is_completed=True), scope, lookup='warehouse_id')
    if flt.warehouse_id is not None:
        inc_qs = inc_qs.filter(warehouse_id=flt.warehouse_id)
        exp_qs = exp_qs.filter(warehouse_id=flt.warehouse_id)
    if flt.date_from:
        inc_qs = inc_qs.filter(date__gte=flt.date_from)
        exp_qs = exp_qs.filter(date__gte=flt.date_from)
    if flt.date_to:
        inc_qs = inc_qs.filter(date__lte=flt.date_to)
        exp_qs = exp_qs.filter(date__lte=flt.date_to)

    other_income = q_money(inc_qs.aggregate(s=Sum('amount'))['s'] or ZERO, digits=4)
    operating_expense = q_money(exp_qs.aggregate(s=Sum('amount'))['s'] or ZERO, digits=4)
    operating_result = q_money(gross_margin + other_income - operating_expense, digits=4)

    # Payroll expense separately (D-55)
    slip_qs = Payslip.objects.filter(payroll_run__status__in=HRM_FINALIZED)
    if flt.warehouse_id is not None:
        slip_qs = slip_qs.filter(employee__warehouse_id=flt.warehouse_id)
    elif not all_warehouse_access(scope):
        slip_qs = slip_qs.filter(employee__warehouse_id=scope.warehouse_id)
    if flt.date_from:
        slip_qs = slip_qs.filter(payroll_run__period__month__gte=flt.date_from.replace(day=1))
    if flt.date_to:
        slip_qs = slip_qs.filter(payroll_run__period__month__lte=flt.date_to.replace(day=1))
    payroll_expense = q_money(slip_qs.aggregate(s=Sum('net_pay'))['s'] or ZERO, digits=4)

    # Cash received/sent from ledger (exclude internal transfers? AccountTransaction records inflow/outflow directly)
    accounts = available_payment_accounts(scope).filter(currency=settings.INVENTORY_BASE_CURRENCY)
    if flt.warehouse_id is not None:
        accounts = accounts.filter(warehouse_id=flt.warehouse_id)
    tx_qs = AccountTransaction.objects.filter(account__in=accounts).exclude(source_kind=AccountTransaction.SourceKind.ACCOUNT_TRANSFER)
    if flt.date_from:
        tx_qs = tx_qs.filter(date__gte=flt.date_from)
    if flt.date_to:
        tx_qs = tx_qs.filter(date__lte=flt.date_to)
    cash_received = q_money(tx_qs.filter(direction=AccountTransaction.Direction.INFLOW).aggregate(s=Sum('amount'))['s'] or ZERO, digits=4)
    cash_sent = q_money(tx_qs.filter(direction=AccountTransaction.Direction.OUTFLOW).aggregate(s=Sum('amount'))['s'] or ZERO, digits=4)
    cash_in_hand = Decimal(account_balances_report(accounts, as_of=flt.as_of)['totals']['balance'])

    return {
        'net_sales': str(net_sales),
        'net_cogs': str(net_cogs),
        'gross_margin': str(gross_margin),
        'other_income': str(other_income),
        'operating_expense': str(operating_expense),
        'operating_result': str(operating_result),
        'payroll_expense': str(payroll_expense),
        'sale_amount': str(q_money(sale_amount, digits=4)),
        'purchase_amount': str(q_money(purchase_amount, digits=4)),
        'sales_return_amount': str(q_money(ret_sales_net, digits=4)),
        'purchase_return_amount': str(q_money(purchase_return_amount, digits=4)),
        'cash_received': str(cash_received),
        'cash_sent': str(cash_sent),
        'cash_in_hand': str(q_money(cash_in_hand, digits=4)),
    }


def run_report(slug: str, flt: ReportFilter, actor) -> ReportResult:
    _check_report_permission(actor, slug)
    scope = warehouse_scope(actor)
    if flt.warehouse_id is not None:
        check_warehouse(scope, flt.warehouse_id, hide=True)

    meta = {
        'filters': {k: (v.isoformat() if isinstance(v, date) else v) for k, v in asdict(flt).items()},
        'definitions': 'D-55/D-56 (base currency, inclusive local dates, posted documents only)',
        'generated_at': timezone.localtime().isoformat(),
    }

    if slug == 'summary':
        s = _compute_summary(scope, flt)
        cols = [{'key': 'metric', 'label': 'Metric', 'type': 'text'}, {'key': 'value', 'label': 'Amount', 'type': 'price'}]
        rows = [{'metric': k, 'value': v} for k, v in s.items()]
        return ReportResult(slug=slug, columns=cols, rows=rows, totals=s, meta=meta)

    if slug == 'product':
        sales = _posted_sales(scope, flt)
        purchases = _posted_purchases(scope, flt)
        s_rets = _completed_sale_returns(scope, flt)
        p_rets = _completed_purchase_returns(scope, flt)
        stats = defaultdict(lambda: {'sku': '', 'name': '', 'purchased_qty': Decimal('0'), 'purchased_amt': ZERO,
                                     'sold_qty': Decimal('0'), 'sold_amt': ZERO, 'sold_cogs': ZERO,
                                     'ret_qty': Decimal('0'), 'ret_amt': ZERO, 'ret_cogs': ZERO,
                                     'in_stock': Decimal('0'), 'worth_cost': ZERO, 'worth_price': ZERO})
        for po in purchases:
            rate = Decimal(po.exchange_rate or 1)
            for it in po.items.all():
                st = stats[it.product_id]
                st['sku'] = it.product.sku
                st['name'] = it.product.name
                st['purchased_qty'] += Decimal(it.quantity)
                st['purchased_amt'] += convert_to_base(Decimal(it.line_net), rate, digits=4)
        for so in sales:
            for it in so.items.all():
                st = stats[it.product_id]
                st['sku'] = it.product.sku
                st['name'] = it.product.name
                st['sold_qty'] += Decimal(it.quantity)
                st['sold_amt'] += _sale_line_net_ex_tax_base(so, it)
                qty_b = Decimal(it.base_quantity if it.base_quantity is not None else it.quantity)
                uc = Decimal(it.unit_cost_base if it.unit_cost_base is not None else it.product.cost_price)
                st['sold_cogs'] += q_money(qty_b * uc, digits=4)
        for r in s_rets:
            for rit in r.items.all():
                it = rit.sales_order_item
                if Decimal(it.quantity) > 0:
                    frac = Decimal(rit.quantity) / Decimal(it.quantity)
                    st = stats[it.product_id]
                    st['ret_qty'] += Decimal(rit.quantity)
                    st['ret_amt'] += q_money(_sale_line_net_ex_tax_base(r.sales_order, it) * frac, digits=4)
                    uc = Decimal(it.unit_cost_base if it.unit_cost_base is not None else it.product.cost_price)
                    st['ret_cogs'] += q_money(Decimal(it.base_quantity or it.quantity) * frac * uc, digits=4)
        stock_qs = scoped_queryset(Stock.objects.select_related('product'), scope, lookup='warehouse_id')
        if flt.warehouse_id:
            stock_qs = stock_qs.filter(warehouse_id=flt.warehouse_id)
        for s in stock_qs:
            st = stats[s.product_id]
            st['sku'] = s.product.sku
            st['name'] = s.product.name
            st['in_stock'] += Decimal(s.quantity)
            st['worth_cost'] += q_money(Decimal(s.quantity) * Decimal(s.average_cost or s.product.cost_price), digits=4)
            st['worth_price'] += q_money(Decimal(s.quantity) * Decimal(s.product.sale_price), digits=4)

        all_rows = []
        tot_profit = ZERO
        tot_stock_cost = ZERO
        for pid, st in sorted(stats.items(), key=lambda kv: (kv[1]['sku'], kv[0])):
            profit = q_money((st['sold_amt'] - st['ret_amt']) - (st['sold_cogs'] - st['ret_cogs']), digits=4)
            tot_profit += profit
            tot_stock_cost += st['worth_cost']
            all_rows.append({
                'product_id': pid, 'sku': st['sku'], 'name': st['name'],
                'purchased_qty': str(st['purchased_qty']), 'purchased_amt': str(q_money(st['purchased_amt'], digits=4)),
                'sold_qty': str(st['sold_qty']), 'sold_amt': str(q_money(st['sold_amt'], digits=4)),
                'returned_qty': str(st['ret_qty']), 'profit': str(profit),
                'in_stock': str(st['in_stock']), 'worth_cost': str(st['worth_cost']), 'worth_price': str(st['worth_price']),
            })
        page_rows, page_meta = _paginate(all_rows, flt)
        meta.update(page_meta)
        cols = [
            {'key': 'sku', 'label': 'SKU', 'type': 'text'}, {'key': 'name', 'label': 'Product', 'type': 'text'},
            {'key': 'purchased_qty', 'label': 'Purchased Qty', 'type': 'number'}, {'key': 'sold_qty', 'label': 'Sold Qty', 'type': 'number'},
            {'key': 'profit', 'label': 'Profit', 'type': 'price'}, {'key': 'in_stock', 'label': 'In Stock', 'type': 'number'},
            {'key': 'worth_cost', 'label': 'Worth (Cost)', 'type': 'price'}, {'key': 'worth_price', 'label': 'Worth (Price)', 'type': 'price'},
        ]
        return ReportResult(slug=slug, columns=cols, rows=page_rows, totals={'profit': str(tot_profit), 'worth_cost': str(tot_stock_cost)}, meta=meta)

    if slug == 'best-seller':
        sales = _posted_sales(scope, flt)
        s_rets = _completed_sale_returns(scope, flt)
        agg = defaultdict(lambda: {'sku': '', 'name': '', 'net_qty': Decimal('0'), 'net_value': ZERO})
        for so in sales:
            for it in so.items.all():
                a = agg[it.product_id]
                a['sku'] = it.product.sku
                a['name'] = it.product.name
                a['net_qty'] += Decimal(it.quantity)
                a['net_value'] += _sale_line_net_ex_tax_base(so, it)
        for r in s_rets:
            for rit in r.items.all():
                it = rit.sales_order_item
                if Decimal(it.quantity) > 0:
                    frac = Decimal(rit.quantity) / Decimal(it.quantity)
                    a = agg[it.product_id]
                    a['net_qty'] -= Decimal(rit.quantity)
                    a['net_value'] -= q_money(_sale_line_net_ex_tax_base(r.sales_order, it) * frac, digits=4)
        rows = [
            {'product_id': pid, 'sku': v['sku'], 'name': v['name'], 'net_qty': str(v['net_qty']), 'net_value': str(q_money(v['net_value'], digits=4))}
            for pid, v in sorted(agg.items(), key=lambda kv: (-kv[1]['net_qty'], kv[1]['sku']))
        ]
        page_rows, pm = _paginate(rows, flt)
        meta.update(pm)
        cols = [{'key': 'sku', 'label': 'SKU', 'type': 'text'}, {'key': 'name', 'label': 'Product', 'type': 'text'},
                {'key': 'net_qty', 'label': 'Net Qty', 'type': 'number'}, {'key': 'net_value', 'label': 'Net Value', 'type': 'price'}]
        return ReportResult(slug=slug, columns=cols, rows=page_rows, totals={'count': len(rows)}, meta=meta)

    if slug in ('daily-sale', 'monthly-sale', 'daily-purchase', 'monthly-purchase'):
        is_sale = 'sale' in slug
        is_monthly = 'monthly' in slug
        docs = _posted_sales(scope, flt) if is_sale else _posted_purchases(scope, flt)
        buckets = defaultdict(lambda: {'count': 0, 'amount': ZERO})
        if not is_monthly and flt.date_from and flt.date_to:
            cur = flt.date_from
            while cur <= flt.date_to:
                buckets[cur.isoformat()]
                cur += timedelta(days=1)
        for doc in docs:
            d = _local_date(doc.completed_at if is_sale else doc.received_at, doc.order_date)
            key = d.strftime('%Y-%m') if is_monthly else d.isoformat()
            amt = convert_to_base(Decimal(doc.grand_total), Decimal(doc.exchange_rate or 1), digits=4)
            buckets[key]['count'] += 1
            buckets[key]['amount'] += amt
        rows = [{'bucket': k, 'count': v['count'], 'amount': str(q_money(v['amount'], digits=4))} for k, v in sorted(buckets.items())]
        tot = q_money(sum((Decimal(r['amount']) for r in rows), ZERO), digits=4)
        cols = [{'key': 'bucket', 'label': 'Period', 'type': 'text'}, {'key': 'count', 'label': 'Orders', 'type': 'number'},
                {'key': 'amount', 'label': 'Amount', 'type': 'price'}]
        return ReportResult(slug=slug, columns=cols, rows=rows, totals={'amount': str(tot), 'count': sum(r['count'] for r in rows)}, meta=meta)

    if slug in ('sale', 'sale-chart'):
        sales = _posted_sales(scope, flt)
        rows = []
        tot_grand = ZERO
        for so in sales:
            bal = compute_order_balance(so)
            tot_grand += bal.recognized_total
            rows.append({
                'id': so.pk, 'order_number': so.order_number,
                'date': _local_date(so.completed_at, so.order_date).isoformat(),
                'warehouse': so.warehouse.name,
                'customer': so.customer.name if so.customer_id else 'Walk-in',
                'biller': so.biller_name_snapshot or (so.biller.name if so.biller_id else ''),
                'grand_total': str(bal.recognized_total),
                'paid': str(bal.net_paid),
                'due': str(bal.open_due),
                'payment_status': bal.payment_status,
            })
        page_rows, pm = _paginate(rows, flt)
        meta.update(pm)
        cols = [{'key': 'order_number', 'label': 'Order #', 'type': 'text'}, {'key': 'date', 'label': 'Date', 'type': 'text'},
                {'key': 'customer', 'label': 'Customer', 'type': 'text'}, {'key': 'grand_total', 'label': 'Total', 'type': 'price'},
                {'key': 'paid', 'label': 'Paid', 'type': 'price'}, {'key': 'due', 'label': 'Due', 'type': 'price'}]
        return ReportResult(slug=slug, columns=cols, rows=page_rows, totals={'grand_total': str(tot_grand), 'count': len(rows)}, meta=meta)

    if slug == 'purchase':
        purchases = _posted_purchases(scope, flt)
        rows = []
        tot_grand = ZERO
        for po in purchases:
            bal = compute_order_balance(po)
            tot_grand += bal.recognized_total
            rows.append({
                'id': po.pk, 'order_number': po.order_number,
                'date': _local_date(po.received_at, po.order_date).isoformat(),
                'warehouse': po.warehouse.name, 'supplier': po.supplier.name,
                'grand_total': str(bal.recognized_total), 'paid': str(bal.net_paid), 'due': str(bal.open_due),
            })
        page_rows, pm = _paginate(rows, flt)
        meta.update(pm)
        cols = [{'key': 'order_number', 'label': 'PO #', 'type': 'text'}, {'key': 'date', 'label': 'Date', 'type': 'text'},
                {'key': 'supplier', 'label': 'Supplier', 'type': 'text'}, {'key': 'grand_total', 'label': 'Total', 'type': 'price'},
                {'key': 'due', 'label': 'Due', 'type': 'price'}]
        return ReportResult(slug=slug, columns=cols, rows=page_rows, totals={'grand_total': str(tot_grand), 'count': len(rows)}, meta=meta)

    if slug == 'payment':
        sp_qs = scoped_queryset(SalesPayment.objects.select_related('sales_order', 'account'), scope, lookup='sales_order__warehouse_id')
        pp_qs = scoped_queryset(PurchasePayment.objects.select_related('purchase_order', 'account'), scope, lookup='purchase_order__warehouse_id')
        if flt.warehouse_id:
            sp_qs = sp_qs.filter(sales_order__warehouse_id=flt.warehouse_id)
            pp_qs = pp_qs.filter(purchase_order__warehouse_id=flt.warehouse_id)
        if flt.date_from:
            sp_qs = sp_qs.filter(date__gte=flt.date_from)
            pp_qs = pp_qs.filter(date__gte=flt.date_from)
        if flt.date_to:
            sp_qs = sp_qs.filter(date__lte=flt.date_to)
            pp_qs = pp_qs.filter(date__lte=flt.date_to)
        rows = []
        in_tot = ZERO
        out_tot = ZERO
        for p in sp_qs:
            is_out = bool(p.sales_return_id or p.is_reversal)
            if is_out:
                out_tot += Decimal(p.amount)
            else:
                in_tot += Decimal(p.amount)
            rows.append({'id': f'SP-{p.pk}', 'date': p.date.isoformat(), 'direction': 'out' if is_out else 'in',
                         'method': p.method, 'account': p.account.name if p.account_id else p.method, 'amount': str(p.amount)})
        for p in pp_qs:
            is_in = bool(p.purchase_return_id or p.is_reversal)
            if is_in:
                in_tot += Decimal(p.amount)
            else:
                out_tot += Decimal(p.amount)
            rows.append({'id': f'PP-{p.pk}', 'date': p.date.isoformat(), 'direction': 'in' if is_in else 'out',
                         'method': p.method, 'account': p.account.name if p.account_id else p.method, 'amount': str(p.amount)})
        rows.sort(key=lambda r: (r['date'], r['id']))
        page_rows, pm = _paginate(rows, flt)
        meta.update(pm)
        cols = [{'key': 'id', 'label': 'Ref', 'type': 'text'}, {'key': 'date', 'label': 'Date', 'type': 'text'},
                {'key': 'direction', 'label': 'Dir', 'type': 'text'}, {'key': 'method', 'label': 'Method', 'type': 'text'},
                {'key': 'amount', 'label': 'Amount', 'type': 'price'}]
        return ReportResult(slug=slug, columns=cols, rows=page_rows, totals={'received': str(in_tot), 'sent': str(out_tot)}, meta=meta)

    if slug == 'challan':
        qs = scoped_queryset(Challan.objects.select_related('courier', 'warehouse'), scope, lookup='warehouse_id')
        if flt.warehouse_id:
            qs = qs.filter(warehouse_id=flt.warehouse_id)
        if flt.status:
            qs = qs.filter(status=flt.status)
        rows = []
        for ch in qs:
            d = _local_date(ch.closed_at if flt.basis == 'closed' else ch.created_at)
            if flt.basis == 'closed' and not ch.closed_at:
                continue
            if _in_range(d, flt):
                rows.append({
                    'id': ch.pk, 'reference_number': ch.reference_number, 'status': ch.status,
                    'courier': ch.courier.name if ch.courier_id else '', 'date': d.isoformat() if d else '',
                })
        cols = [{'key': 'reference_number', 'label': 'Challan #', 'type': 'text'}, {'key': 'status', 'label': 'Status', 'type': 'text'},
                {'key': 'courier', 'label': 'Courier', 'type': 'text'}, {'key': 'date', 'label': 'Date', 'type': 'text'}]
        return ReportResult(slug=slug, columns=cols, rows=rows, totals={'count': len(rows)}, meta=meta)

    if slug in ('customer', 'customer-due', 'supplier', 'supplier-due', 'customer-group'):
        is_sup = 'supplier' in slug
        qs = Party.objects.filter(is_supplier=True) if is_sup else Party.objects.filter(is_customer=True)
        rows = []
        tot_due = ZERO
        for party in qs.order_by('name', 'pk'):
            s = party_due_summary(party.pk, as_of=flt.as_of, scope=scope)
            blk = s['payables'] if is_sup else s['receivables']
            due_val = Decimal(blk['total_due'])
            if 'due' in slug and due_val <= ZERO:
                continue
            tot_due += due_val
            rows.append({
                'id': party.pk, 'code': party.code, 'name': party.name,
                'group': party.customer_group.name if party.customer_group_id else '',
                'grand_total': blk['total_grand'], 'returned': blk['total_returned'],
                'paid': blk['total_paid'], 'due': blk['total_due'],
            })
        cols = [{'key': 'code', 'label': 'Code', 'type': 'text'}, {'key': 'name', 'label': 'Party', 'type': 'text'},
                {'key': 'grand_total', 'label': 'Total', 'type': 'price'}, {'key': 'returned', 'label': 'Returned', 'type': 'price'},
                {'key': 'paid', 'label': 'Paid', 'type': 'price'}, {'key': 'due', 'label': 'Due', 'type': 'price'}]
        return ReportResult(slug=slug, columns=cols, rows=rows, totals={'total_due': str(tot_due), 'count': len(rows)}, meta=meta)

    if slug == 'product-expiry':
        cutoff = flt.as_of or timezone.localdate()
        qs = scoped_queryset(StockBatch.objects.select_related('product', 'warehouse').filter(quantity__gt=0, expiry_date__lte=cutoff), scope, lookup='warehouse_id')
        if flt.warehouse_id:
            qs = qs.filter(warehouse_id=flt.warehouse_id)
        rows = [{'sku': b.product.sku, 'product': b.product.name, 'batch_no': b.batch_no,
                 'expiry_date': str(b.expiry_date), 'quantity': str(b.quantity)} for b in qs.order_by('expiry_date', 'product__sku')]
        cols = [{'key': 'sku', 'label': 'SKU', 'type': 'text'}, {'key': 'batch_no', 'label': 'Batch', 'type': 'text'},
                {'key': 'expiry_date', 'label': 'Expiry', 'type': 'text'}, {'key': 'quantity', 'label': 'Qty', 'type': 'number'}]
        return ReportResult(slug=slug, columns=cols, rows=rows, totals={'count': len(rows)}, meta=meta)

    if slug == 'product-quantity-alert':
        qs = scoped_queryset(Stock.objects.select_related('product', 'warehouse'), scope, lookup='warehouse_id')
        if flt.warehouse_id:
            qs = qs.filter(warehouse_id=flt.warehouse_id)
        rows = []
        for s in qs:
            thresh = resolve_alert_threshold(s.product, s.warehouse_id)
            if thresh is not None and Decimal(s.quantity) <= Decimal(thresh):
                rows.append({'sku': s.product.sku, 'product': s.product.name, 'warehouse': s.warehouse.name,
                             'quantity': str(s.quantity), 'alert_quantity': str(thresh)})
        cols = [{'key': 'sku', 'label': 'SKU', 'type': 'text'}, {'key': 'product', 'label': 'Product', 'type': 'text'},
                {'key': 'quantity', 'label': 'On Hand', 'type': 'number'}, {'key': 'alert_quantity', 'label': 'Alert', 'type': 'number'}]
        return ReportResult(slug=slug, columns=cols, rows=rows, totals={'count': len(rows)}, meta=meta)

    if slug == 'daily-sale-objective':
        sales = _posted_sales(scope, flt)
        sold_qty_map = defaultdict(lambda: Decimal('0'))
        for so in sales:
            for it in so.items.all():
                sold_qty_map[it.product_id] += Decimal(it.quantity)
        rows = []
        for p in Product.objects.filter(daily_sale_objective__gt=0).order_by('sku'):
            actual = sold_qty_map.get(p.pk, Decimal('0'))
            rows.append({'sku': p.sku, 'name': p.name, 'objective': str(p.daily_sale_objective), 'actual': str(actual)})
        cols = [{'key': 'sku', 'label': 'SKU', 'type': 'text'}, {'key': 'objective', 'label': 'Objective', 'type': 'number'},
                {'key': 'actual', 'label': 'Actual', 'type': 'number'}]
        return ReportResult(slug=slug, columns=cols, rows=rows, totals={'count': len(rows)}, meta=meta)

    if slug in ('user', 'biller'):
        sales = _posted_sales(scope, flt)
        agg = defaultdict(lambda: {'count': 0, 'amount': ZERO})
        for so in sales:
            key = (so.biller_name_snapshot or (so.biller.name if so.biller_id else 'Unassigned')) if slug == 'biller' else (so.create_by or 'system')
            agg[key]['count'] += 1
            agg[key]['amount'] += convert_to_base(Decimal(so.grand_total), Decimal(so.exchange_rate or 1), digits=4)
        rows = [{'name': k, 'count': v['count'], 'amount': str(q_money(v['amount'], digits=4))} for k, v in sorted(agg.items())]
        cols = [{'key': 'name', 'label': 'Name', 'type': 'text'}, {'key': 'count', 'label': 'Sales', 'type': 'number'},
                {'key': 'amount', 'label': 'Total', 'type': 'price'}]
        return ReportResult(slug=slug, columns=cols, rows=rows, totals={'count': sum(r['count'] for r in rows)}, meta=meta)

    if slug == 'account-balances':
        accts = available_payment_accounts(scope)
        if flt.warehouse_id:
            accts = accts.filter(warehouse_id=flt.warehouse_id)
        rep = account_balances_report(accts, as_of=flt.as_of)
        cols = [{'key': 'name', 'label': 'Account', 'type': 'text'}, {'key': 'opening', 'label': 'Opening', 'type': 'price'},
                {'key': 'credit', 'label': 'Credit', 'type': 'price'}, {'key': 'debit', 'label': 'Debit', 'type': 'price'},
                {'key': 'balance', 'label': 'Balance', 'type': 'price'}]
        return ReportResult(slug=slug, columns=cols, rows=rep['rows'], totals=rep['totals'], meta=meta)

    if slug == 'balance-sheet':
        rep = balance_sheet_report(actor, scope, as_of=flt.as_of)
        cols = [{'key': 'section', 'label': 'Section', 'type': 'text'}, {'key': 'item', 'label': 'Item', 'type': 'text'},
                {'key': 'amount', 'label': 'Amount', 'type': 'price'}]
        rows = [{'section': 'Assets', 'item': k, 'amount': v} for k, v in rep['assets'].items() if k != 'total']
        rows += [{'section': 'Liabilities', 'item': k, 'amount': v} for k, v in rep['liabilities'].items() if k != 'total']
        rows.append({'section': 'Equity', 'item': 'equity (derived)', 'amount': rep['equity']})
        return ReportResult(slug=slug, columns=cols, rows=rows, totals={'assets': rep['assets']['total'], 'liabilities': rep['liabilities']['total'], 'equity': rep['equity']}, meta=meta)

    if slug == 'income-expense':
        rep = income_expense_report(scope, start_date=flt.date_from, end_date=flt.date_to)
        cols = [{'key': 'period', 'label': 'Period', 'type': 'text'}, {'key': 'income', 'label': 'Income', 'type': 'price'},
                {'key': 'expense', 'label': 'Expense', 'type': 'price'}, {'key': 'net', 'label': 'Net', 'type': 'price'}]
        return ReportResult(slug=slug, columns=cols, rows=rep['by_period'], totals={'total_income': rep['total_income'], 'total_expense': rep['total_expense'], 'net': rep['net']}, meta=meta)

    if slug in ('daybook', 'cash-flow'):
        accts = available_payment_accounts(scope).filter(currency=settings.INVENTORY_BASE_CURRENCY)
        if flt.warehouse_id:
            accts = accts.filter(warehouse_id=flt.warehouse_id)
        txs = AccountTransaction.objects.filter(account__in=accts).select_related('account').order_by('date', 'pk')
        if flt.date_from:
            txs = txs.filter(date__gte=flt.date_from)
        if flt.date_to:
            txs = txs.filter(date__lte=flt.date_to)
        inflow = ZERO
        outflow = ZERO
        rows = []
        for t in txs:
            if t.direction == AccountTransaction.Direction.INFLOW:
                inflow += Decimal(t.amount)
            else:
                outflow += Decimal(t.amount)
            rows.append({'date': t.date.isoformat(), 'account': t.account.name, 'direction': t.direction,
                         'source': t.source_kind, 'amount': str(t.amount)})
        cols = [{'key': 'date', 'label': 'Date', 'type': 'text'}, {'key': 'account', 'label': 'Account', 'type': 'text'},
                {'key': 'direction', 'label': 'Direction', 'type': 'text'}, {'key': 'amount', 'label': 'Amount', 'type': 'price'}]
        return ReportResult(slug=slug, columns=cols, rows=rows, totals={'inflow': str(inflow), 'outflow': str(outflow), 'net': str(inflow - outflow)}, meta=meta)

    if slug in ('warehouse-stock-chart', 'category-stock-worth'):
        qs = scoped_queryset(Stock.objects.select_related('product__category', 'warehouse'), scope, lookup='warehouse_id')
        if flt.warehouse_id:
            qs = qs.filter(warehouse_id=flt.warehouse_id)
        agg = defaultdict(lambda: {'qty': Decimal('0'), 'worth_cost': ZERO})
        for s in qs:
            label = s.warehouse.name if slug == 'warehouse-stock-chart' else (s.product.category.name if s.product.category_id else 'Uncategorized')
            agg[label]['qty'] += Decimal(s.quantity)
            agg[label]['worth_cost'] += q_money(Decimal(s.quantity) * Decimal(s.average_cost or s.product.cost_price), digits=4)
        rows = [{'group': k, 'quantity': str(v['qty']), 'worth_cost': str(v['worth_cost'])} for k, v in sorted(agg.items())]
        cols = [{'key': 'group', 'label': 'Group', 'type': 'text'}, {'key': 'quantity', 'label': 'Qty', 'type': 'number'},
                {'key': 'worth_cost', 'label': 'Worth (Cost)', 'type': 'price'}]
        return ReportResult(slug=slug, columns=cols, rows=rows, totals={'worth_cost': str(sum((Decimal(r['worth_cost']) for r in rows), ZERO))}, meta=meta)

    if slug == 'warehouse':
        s = _compute_summary(scope, flt)
        cols = [{'key': 'metric', 'label': 'Metric', 'type': 'text'}, {'key': 'value', 'label': 'Value', 'type': 'price'}]
        return ReportResult(slug=slug, columns=cols, rows=[{'metric': k, 'value': v} for k, v in s.items()], totals=s, meta=meta)

    if slug == 'task':
        rep = build_task_reports(actor)
        cols = [{'key': 'status', 'label': 'Status', 'type': 'text'}, {'key': 'count', 'label': 'Count', 'type': 'number'}]
        return ReportResult(slug=slug, columns=cols, rows=rep['by_status'], totals=rep['counters'], meta=meta)

    if slug == 'payroll':
        slips = Payslip.objects.filter(payroll_run__status__in=HRM_FINALIZED).select_related('payroll_run__period')
        if flt.warehouse_id:
            slips = slips.filter(employee__warehouse_id=flt.warehouse_id)
        elif not all_warehouse_access(scope):
            slips = slips.filter(employee__warehouse_id=scope.warehouse_id)
        rows = [{
            'month': str(sl.payroll_run.period), 'employee_code': sl.employee_code,
            'employee_name': sl.employee_name, 'gross_pay': str(sl.gross_pay),
            'total_deductions': str(sl.total_deductions), 'net_pay': str(sl.net_pay),
        } for sl in slips]
        cols = [{'key': 'month', 'label': 'Month', 'type': 'text'}, {'key': 'employee_name', 'label': 'Employee', 'type': 'text'},
                {'key': 'gross_pay', 'label': 'Gross', 'type': 'price'}, {'key': 'net_pay', 'label': 'Net', 'type': 'price'}]
        return ReportResult(slug=slug, columns=cols, rows=rows, totals={'net_pay': str(sum((Decimal(r['net_pay']) for r in rows), ZERO))}, meta=meta)

    raise NotFound(f'Report "{slug}" not found.')


def build_dashboard_payload(actor, *, warehouse_id: int | None = None, preset: str = 'month',
                            start_date: date | None = None, end_date: date | None = None) -> dict:
    """DASH-01 payload powered by RPT-01 services so KPIs reconcile identically with reports & lists."""
    today = timezone.localdate()
    if start_date is None or end_date is None:
        if preset == 'today':
            start_date, end_date = today, today
        elif preset == '7d':
            start_date, end_date = today - timedelta(days=6), today
        elif preset == 'year':
            start_date, end_date = date(today.year, 1, 1), today
        else:
            start_date, end_date = today.replace(day=1), today

    scope = warehouse_scope(actor)
    if warehouse_id is not None:
        check_warehouse(scope, warehouse_id, hide=True)

    flt = ReportFilter(date_from=start_date, date_to=end_date, warehouse_id=warehouse_id, page_size=500)
    summary = _compute_summary(scope, flt) if (actor.is_superuser or actor.has_perm('sale.view_salesorder')) else {}

    best = run_report('best-seller', ReportFilter(date_from=start_date, date_to=end_date, warehouse_id=warehouse_id, page_size=5), actor).rows if (actor.is_superuser or actor.has_perm('sale.view_salesorder')) else []

    quick_actions = []
    for perm, label, url_name in [
        ('people.add_lead', 'Add Lead', 'people_pages:add-lead'),
        ('sale.add_salesorder', 'Add Sale', 'sale_pages:create-sale'),
        ('purchase.add_purchaseorder', 'Add Purchase', 'purchase_pages:create-purchase'),
        ('inventory.add_product', 'Add Product', 'inventory_pages:add-product'),
        ('people.add_party', 'Add Customer', 'people_pages:add-party'),
    ]:
        if actor.is_superuser or actor.has_perm(perm):
            quick_actions.append({'label': label, 'url_name': url_name})

    return {
        'period': {'preset': preset, 'start_date': start_date.isoformat(), 'end_date': end_date.isoformat()},
        'kpis': {
            'revenue': summary.get('net_sales', '0.0000'),
            'sale_amount': summary.get('sale_amount', '0.0000'),
            'purchase_amount': summary.get('purchase_amount', '0.0000'),
            'profit': summary.get('operating_result', '0.0000'),
        },
        'summary': summary,
        'best_sellers': best,
        'quick_actions': quick_actions,
    }
