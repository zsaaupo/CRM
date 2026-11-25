# Sale ERP parity plan for eManager

**Review date:** 24 November 2026 · **Target:** Django project `eManager_project` · **Baseline:** `feature/automatic-payments` at `261c0fa` · **Reference:** [authenticated Sale ERP](https://saleerp.emanageritpark.com/dashboard)

## 1. What to develop first

Start with **a completed sale that can still have money due**. Today `sale.services.complete_sale` posts inventory and calls `post_completed_sale_payment` in the same transaction. `purchase.services.receive_purchase` likewise receives the entire order and posts a full payment. The reference exposes independent sale and payment status, an Add Payment action on both order lists, and a customer due report with separate returned, paid and due amounts. Adding a POS screen on top of the current completion behavior would silently settle credit sales. Implement explicit settlement first, then build register checkout on that contract.

The next shared dependency is **a record of why stock changed**. Local stock is a balance per warehouse and product. Transfers, receipts, sales and returns already update it safely through dedicated services. Batches, partial receipts, physical counts, production and reliable historical margin all need the movement identity, quantity, unit and cost behind that balance. Introduce this through the existing posting services.

**Scope of evidence.** I signed in with the supplied administrator account, expanded the business menus, inspected forms, list actions, report outputs and settings, and compared them with the current local models, routes, services, templates, migrations and tests. The reference evidence is its user interface. Its server code, database constraints, background jobs and the effects of posting live transactions were unavailable for verification. In this plan, **observed** means visible on a reference screen, **local** identifies existing source behavior, and **proposed** identifies a design or acceptance rule for eManager. Links to live screens require sign-in. No live business documents, payments, settings, messages or backups were submitted.

## 2. Parity map

| Reference area | Current local implementation | Remaining development |
|---|---|---|
| Dashboard | `account/page_views.py::DashboardPageView` and `account/templates/account/dashboard.html` show account/profile data | Warehouse and date controls, revenue, sales, purchases, profit, cash flow, yearly series, shortcuts, latest five by document type, top products by quantity/value |
| Warehouse/Branch | `warehouse` provides branches, stock lists, transfers, scoped activity reports and a transfer PDF | Stock chart and valuation, staged transfers, transfer cost/tax/shipping, attachments and CSV, branch import |
| Raw Material | No production order, raw-material stock or BOM domain in the active apps | Material master and warehouse balances, costed and versioned bills of materials, requirement preview, production draft and atomic posting |
| Leads | `people.Lead`, list/detail/edit, assignment, dates, notes and all six reference statuses exist | Verify search/status filters and list actions against local UI. No separate CRM rebuild identified. The reference detail did not establish automatic customer creation on conversion |
| Product | Category, brand, SKU, barcode, unit, currency, product types, variant parent, attributes, cost, sale/wholesale price and product tax exist | Product-specific sale/purchase units, warehouse prices, combos, tracking, expiry, opening stock, featured and promotional selection, objectives, named tax rules, label printing and physical counts |
| Purchase | Header/lines, supplier, exchange-rate snapshot, tax, discounts, freight, receive once, returns and PDF exist | Partial receiving, separate settlement, supplier balance and payment history interface, batches, document-level pricing, attachments, duplicate/import, production entry from purchase screen |
| Sale / POS | Header/lines, customer, dates, addresses, FX, stock completion, returns, payment history and sales-order PDF exist | Checkout and registers, multi-tender, credit, order discounts, biller, sale/staff notes, gifts, coupons, loyalty, delivery documents, invoice formats, CSV, duplicate and messaging |
| Expense / Income | `accounting` has categories, entries, completion, account transactions and payment histories | Notes/reference parity, filtered summaries and category imports where exposed. Map both reference accounting and standalone menu entries onto the same local records |
| Quotation | Issue snapshot, accept, decline, expiry, revise and convert to sales order exist | Biller and optional supplier, purchase conversion, attachments, document-level pricing, display mapping for Pending/Sent |
| Return | Sales and purchase returns link original lines, cap quantities, post stock changes and generate refunds | Reconcile unpaid and split-paid returns, batch/serial selection, partial-receipt limits and return-adjusted dues |
| Accounting | Cash/bank/mobile accounts, income/expense, ledger, account statements, cash flow, daybook, money/fund transfers exist | Account numbers and cross-account balance view, income/expense summary, manual settlement and payable/receivable reconciliation |
| HRM | Employees, departments/designations, effective compensation/schedules, leave approval, holidays, attendance, payroll reviews, payments, loans and payslip PDFs exist | Promotion history, notices, device attendance, richer shift/late policies, leave quotas/carryover, month policy freeze, salary-advance workflow and manual payroll parity |
| Task Management | No equivalent app or mounted task routes | Tasks, subtasks, groups, assignments, responsibility views, seen state and reports |
| People | Django users and roles plus a single soft-deletable `Party` that can be customer and supplier | Billers, commercial customer groups, optional customer login link, deposits, loyalty, discount assignment, due balances and imports |
| Reports | Document PDFs, payroll reports, warehouse activity, daybook, cash flow and ledgers exist | Cross-module analytical reports and dashboard metrics in §7. A downloadable order is a different deliverable from a period sales report |
| Settings | Groups/permissions, warehouse access, profile, operational lookup settings and configuration in project settings exist | Operator-editable business/POS settings, rule catalogs, delivery channels, custom fields, barcode layouts, branding and languages |
| Audit Logs | Creator/modifier fields, payroll events and financial postings provide partial history | Cross-module searchable action history with entity, actor, timestamp, details and scoped access |

## 3. Local integration points

| Concern | Code to extend | Contract to preserve |
|---|---|---|
| Application entry | `eManager_project01/urls.py`; each app's `api_urls.py` and `page_urls.py`; `report/urls.py` | Implement business actions on the Django/DRF side. Wire server-rendered pages to the same action services. Keep the existing public URL conventions |
| Permission and branch scope | `account/permissions.py`, `warehouse/scope.py`, `warehouse/access.py`, `warehouse/activity.py` | Apply permission AND warehouse scope to mutations, lookups, report totals, downloads and exports. Recheck access under lock on financial/stock commands |
| Inventory | `inventory/models.py`, `warehouse/models.py`, `warehouse/services.py` | Product SKU and barcode uniqueness, protected units, and one stock row per warehouse/product. Current active models are authoritative. Some tracking fields in early migrations were subsequently removed |
| Procurement/sales | `purchase/services.py::receive_purchase`, `sale/services.py::complete_sale`, both apps' `returns.py` | Keep once-only claims, guarded stock updates, rollback on error, completed-document protection and quantity caps. Handle SQLite locking as the current services do |
| Quotations | `sale/services.py::apply_quotation_action`, `sale/models.py::SalesQuotation` | Issued-document snapshot, validity dates, permission checks and revision lineage survive changes to catalogs and prices |
| Finance | `accounting/services.py`, `accounting/api_views.py`, `accounting/book_reports.py` | AccountTransaction direction and originating payment/transfer remain traceable. Existing linked payments and opening balances are the financial baseline |
| Workforce | `hrm/payroll.py`, `hrm/payroll_inputs.py`, `hrm/payroll_workflow.py`, `hrm/services.py`, `hrm/payroll_reports.py` | Preserve effective-dated salary structures, digest-checked preview, run approval, immutable payment outcomes, installment recovery and stored payslip inputs |
| Rendering | `report/catalog.py`, `report/services.py`, `report/rendering.py`, `report/templates/` | Reuse the PDF renderer and fonts for invoices, labels, packing slips and challans. Existing sale output is titled **Sales order** |

## 4. Stock, product and manufacturing work packages

### INV-01 · Record inventory movements and posting-time costs

**Observed** — The product report combines purchased/sold/returned quantities and amounts, profit, on-hand and stock worth at selling price and cost. The reference also exposes warehouse stock, batch and expiry controls. **Local** — `Stock(warehouse, product, quantity)` has no movement journal or lot dimension. Sale lines store sale price, discount and tax. A later change to `Product.cost_price` must not change historical margin.

**Proposed implementation**

1. Add an append-only `StockMovement` in `warehouse`. Store warehouse, SKU, signed base quantity, base unit, movement date, posting timestamp, unit cost in base currency, source document/line, movement kind and a unique posting key. Allow several distinct events against a line for partial receipts/returns. Add lot/serial allocations when tracking is enabled.
2. Implement a shared posting function callable inside the current atomic transaction. Lock warehouse/SKU/lot in stable order, validate quantity and apply movement plus existing balance together. For a completed transfer, post matching out/in legs. A failure in any leg rolls back all legs and any associated cash posting.
3. Snapshot cost at sale completion. Proposed initial choice for newly costed receipts is moving weighted average per warehouse/SKU. Carry the source cost on transfers and the original issue cost on sale returns. Record an explicit policy/version if choosing another basis. The reference does not reveal its inventory costing algorithm.
4. At cutover, record a dated opening movement matching each current balance and identify the provenance of its cost. Do not replay historical orders into current stock. Tag missing historical costs as unknown and expose coverage in margin reports.

**Acceptance** — Opening 5 plus receipt 10 minus sale 4 plus sale return 1 leaves 12 units. Movement totals and `Stock.quantity` agree. Two simultaneous completions produce one business event. A later catalog price edit leaves the posted margin unchanged.

### INV-02 · Units, product behavior, prices and tracking

**Observed** — Add Product exposes Standard, Combo, Digital and Service, independent sale/purchase units, wholesale price, alert quantity, daily objective, tax method, initial stock, featured, variants, different prices by warehouse, batch/expiry, IMEI/serial and promotional price. A standard, variant-free product can be selected for in-house production. **Local** — Goods/service/digital type support and variant SKUs already exist. Current local order-line validation requires the product's base unit.

| Change | Data and behavior | Acceptance |
|---|---|---|
| Units and combos | Add product-unit conversion numerator/denominator, default buying/selling units and combo components. Snapshot the conversion per line. Define whether a combo issues components or its assembled stock. Service/digital item types need explicit fulfillment rules | Buying 2 boxes of 12 adds 24 base units. Selling 3 pieces leaves 21. A service sale completes without decrementing a physical balance. A component cannot recursively contain its combo |
| Price and tax | Add effective-dated warehouse price, named tax rate/method, promo price and validity, featured flag and per-product daily objective. Retain current catalog defaults. Save selected price, rule, rate and tax basis on document lines | An expired promotion falls back to the resolved warehouse/catalog price. The same tax-inclusive price produces the same totals on quote, invoice and return |
| Tracking | Add product tracking policy, lot with batch/expiry, serial identity, location/state and document allocations. Require serial count to match integral units. Record original lot/serial on returns; define expiry warning/block as a setting | One serial cannot be sold twice. A February-expiring lot appears in the correct expiry filter. Invalid allocation blocks the whole posting |
| Stock thresholds and openings | Store warehouse/product reorder thresholds with optional product default. Enter opening stock as an audited movement and restrict retrospective changes | Threshold report uses the selected warehouse. Repeating an opening import leaves one movement |
| Labels | Create barcode symbology and sheet-layout settings, label contents, quantity per SKU and print preview. Reuse the existing barcode field and PDF pipeline | Three copies of two distinct SKUs yield the requested six labels and decode to the correct product codes |

### INV-03 · Physical count, adjustment and staged transfer

**Observed** — Count Stock supports Full/Partial per warehouse, initial file, final file and Finalize. Add Adjustment selects warehouse, document, product, cost, quantity, action and note. Transfer has Pending, Sent and Completed, source/destination, batch, unit cost, tax, shipping, document and CSV. **Local** — Transfer is an atomic one-step operation controlled by `is_completed`. Stock has read APIs. Local tests explicitly cover removal of manual balance editing.

**Proposed** — Add `StockCount` and `StockCountLine` with snapshot time, expected, counted, cutoff movement and variance. On finalize either reject changed SKUs for recount or reconcile intervening movements explicitly. Post a reasoned `StockAdjustment` once. Support full/partial sets and a validated final-file import. Add dispatch/receive events to `WarehouseTransfer` and track in-transit stock. The existing completion action can atomically perform both steps for direct transfers. Add attachment, cost and shipping metadata. Verify how the reference allocates freight before including it in inventory cost.

**Acceptance** — Expected 12 and counted 10 produces a single −2 adjustment. A sale during counting cannot disappear on finalization. Sending 3 from A removes 3 from A and shows 3 in transit. Receiving moves those 3 into B. Duplicate receipt has zero additional effect.

### MFG-01 · Raw materials, BOM and in-house production

**Observed** — Raw material has name, SKU, category, stock unit, current unit cost, reorder point, default warehouse and specifications. Raw Material Stock shows material, warehouse, on hand, reorder point, status and unit cost. A BOM has code, name, finished product, version, base output, status and component rows with unit, quantity per base output, wastage %, Net/Gross basis and issue multiple. The production form requires an **approved BOM**, material and finished-goods warehouses, planned/accepted quantities and optional rejects. It previews required, available, shortage, actual used and estimated cost, accepts labor/overhead, and offers Save Draft and Post Production. The form explicitly says drafts leave stock unchanged and posting has no supplier payable. [Purchase/production form](https://saleerp.emanageritpark.com/purchases/create) · [BOM register](https://saleerp.emanageritpark.com/boms)

**Proposed implementation**

1. Add a `manufacturing` app with a raw-material profile linked to an inventory SKU, or a distinct raw-material identity with a common movement adapter. Enforce unique material code and a valid stock unit. Provide an opening/receipt path with source and cost so that the preview can actually find inventory. The reference material replenishment entry point was not established.
2. Add `BillOfMaterials`, `BOMComponent`, `ProductionOrder` and actual consumption lines. Version BOMs and freeze approved versions used by orders. Reject incompatible units, invalid base output, component cycles and ineligible finished SKUs. Show only approved matching versions in production.
3. Preview scales component quantities by planned output/base output, applies the selected wastage basis and issue multiple, and returns shortage by source warehouse. **Confirm the site's Net/Gross formula and rounding order with a worked example** before claiming numeric parity. Store resolved quantities on a posted order.
4. Drafts can be edited. On post, recheck permissions, BOM, on-hand and actual usage under lock. Consume actual material, receive accepted goods once, record rejected quantity separately, and snapshot material/labor/overhead costs. Proposed unit cost = (actual material cost + labor + overhead) / accepted good quantity. Define all-rejected disposition separately. No supplier order or payment is created.
5. Expose material/list/stock, BOM create/approve, production preview/draft/post/list/detail. Link the purchase page's In-House Production switch to the production service. Add a configuration flag for visibility and server-side eligibility.

**Acceptance** — BOM for 10 finished units uses 4 kg with zero waste. A planned batch of 20 previews 8 kg. Actual 8 kg at 50 plus labor 80 and overhead 60 yields cost 540. Accepting 18 and rejecting 2 receives 18 units at 30 each. A shortage at commit rolls back all movements. Repeating Post has no further effect or cash entry.

## 5. Trading, settlement and fulfillment

### FIN-01 · Credit, partial and split settlement

**Observed** — Sale status and payment status are separate controls. Payment status choices include Pending, Due, Partial and Paid. Sales and purchases expose View Payment and Add Payment. POS displays Cash, Card, Multiple Payment, Cheque, Gift Card and Deposit. Customer and supplier lists expose dues. **Local** — `post_completed_sale_payment` collects the outstanding amount, `post_received_purchase_payment` pays the full converted total, and sales payment listing is read-only. Purchase refund code currently chooses one original payment; `PurchasePayment.purchase_return` is one-to-one.

1. Add an explicit settlement mode to new commands. Preserve legacy automatic settlement by default for existing clients. For deferred terms allow a nullable payment account until a collection/disbursement requires one. Update serializer, page options, quote conversion, completion and receiving together. Let a caller intentionally pay in full using the same underlying payment command.
2. Add scoped collection, supplier payment and reversal commands. Store payment method separately from the financial account's type. Add idempotency to purchase payments and split tender allocations. Lock the order, then affected accounts in stable order. Reject conflicting key reuse, invalid currency, excessive amounts and amounts that round to zero. Use stored order FX for document-to-base reconciliation.
3. Derive balances from posted events. For an order expressed in one currency: **open balance = recognized document total − posted return credits − payments applied + refunds issued**. Track customer credit separately if policy allows an excess credit. Distinguish sale completion, receipt state and settlement state. Define business rules for Pending versus Due, and for recognizing a payable on order, invoice or receipt.
4. On a return reduce the open balance first under the proposed policy, then refund cash previously collected/paid up to the remaining credit. Preserve the original tender/account trail. Replace the purchase refund's single-payment assumption with linked allocations and a migration for existing one-to-one refunds. Prevent the same value from becoming both cash and customer deposit.
5. Add customer/supplier statement and due query services. The reference customer menu includes Due Report, Clear Due, Add Deposit (amount and note) and View Deposit. The supplier menu also offers Clear Due. Implement Clear Due as a settlement preview showing which open documents would receive the amount and in what order. Confirm allocation explicitly, then persist the receipt/disbursement and its document allocations atomically. Route lists, dashboard and reports through the same calculation. Keep transfers separate from trading receipts in company-level reports.

**Migration blocker in this branch** — `accounting_purchase_original_payment_unique` allows one original payment per purchase. Adding a POST endpoint without replacing this constraint will reject the second installment. Introduce a unique command/allocation identity, backfill keys for existing rows, then replace the per-order uniqueness. Keep the once-only legacy auto-payment guard at its source event. Each `AccountTransaction` currently requires exactly one valid source. Add deposits, drawer movements or actual payroll disbursements through a recognized source and extend that constraint atomically. A gift-card/deposit redemption consumes stored value; it should not generate a second external cash receipt.

**Acceptance, in BDT** — Post a 1,000 sale on credit and collect 400. Stock falls once, cash rises by 400, due is 600. A return credit of 250 lowers due to 350 with no cash refund. In a separate fully paid sale of 1,000, a 250 return/refund leaves due zero and net collections 750. Splitting 400 cash + 600 card produces two tender lines, one invoice and no second automatic collection. Repeat for supplier payments, negative cases and retries.

### TRADE-01 · Partial receipts and document totals

**Observed** — Purchase status offers Ordered, Pending, Partial and Received. Purchase lines include batch, expiry, cost, discount and tax. Sale supports flat/percentage order discount, order tax, shipping, attachment and separate sale/staff notes. Both store currency/exchange rate on entry. The sale/purchase menus expose CSV import. **Local** — Configurable `TradeStatus` plus booleans `is_received` and `is_completed`; order totals already include line discounts, tax and shipping. Status labels alone cannot represent physical receipts.

**Proposed** — Add `PurchaseReceipt` and lines keyed to original order lines, with received quantity, base quantity, lot/serial and cost. Lock outstanding quantities, support multiple receipts, and derive receipt state from line totals. The legacy receive action receives outstanding quantities through the new service. Preserve the historic source when backfilling returns. Extend sale, purchase and quote models with document-level discounts/taxes, attachments and typed notes where appropriate. Centralize price calculation and freeze the tax base, discount allocations and rounding residual on posting. Define whether header tax compounds with line tax and whether shipping is refundable. Duplicate copies editable business fields into a draft with a fresh reference, empty payments and zero postings.

**Acceptance** — Receive 4 then 6 against an order of 10. Each receipt adds only its quantity. Returning 5 after the first receipt fails. Create an invoice with item discounts and a header percentage, then return all items across two returns. Cumulative refundable merchandise matches the posted document under its stored rounding policy.

### QUO-01 · Supplier linkage and purchase conversion

**Observed** — Add Quotation requires customer, biller and warehouse, accepts a supplier, and exposes Pending/Sent. An existing sent quotation's action menu offered both **Create Sale** and **Create Purchase**. **Local** — Only accepted quotations can convert to a sales order; conversion copies lines and validates a payment account.

**Proposed** — Add nullable biller and supplier FKs and an auditable link from a quotation revision to a generated purchase. Offer an explicit purchase-conversion action with both quote-read and purchase-create permissions. Require an active supplier before creating; preview purchase unit and purchase cost for each line. The site's rule for copying selling price versus buying price was not verified. Require a deliberate cost selection. Store the source revision and make a retry return the same draft purchase. Preserve the current sales approval route. Decide if sales and procurement conversion may both occur for a revision, or are mutually exclusive. Track outcomes separately if both are allowed.

**Acceptance** — An authorized user converts an eligible quotation to a draft purchase; repeating the request returns the same purchase. Issuing or converting a quotation does not itself affect stock or cash. A supplier-only record cannot fill the required customer field. Historic issued PDFs retain their original prices.

### POS-01 · Checkout, cash register and invoice

**Observed** — POS exposes category/brand/featured product browsing, customer, quantities, totals, draft, recent transactions and tender controls. Register list shows user, warehouse, cash in hand, opened/closed timestamps and status. Settings include default customer/biller/warehouse, product row count, touchscreen keyboard, tables, SMS after sale, A4 or thermal receipt (58/80 mm) and payment options.

**Proposed** — Build a POS page in `sale` and one checkout command over TRADE-01 and FIN-01. Add `RegisterSession` (user, warehouse, opening float, opened/closed time, counted cash, variance), sale/register link and tender allocations. Validate warehouse access to defaults. A draft saves a resumable cart. Checkout reloads current availability, confirms final price, posts sale/stock/payment allocations once and produces an invoice snapshot. Define whether one open session per user/branch or per physical terminal is allowed. Expected drawer = opening float + cash received − cash refunds ± documented drawer movements. Exclude non-cash tenders from drawer totals. Support receipt reprints and a linked, optional table assignment.

**Acceptance** — Open with 500, sell for 1,000 using 400 cash and 600 card, then refund 100 in cash. Expected drawer is 800. Reprinting and retrying checkout leave both ledger and stock unchanged. A draft sale creates no movement. Test A4, 58 mm and 80 mm output, including Bangla item names and long descriptions.

### PRICE-01 · Promotions, deposits, gift cards and points

| Reference evidence | Proposed local behavior | Acceptance |
|---|---|---|
| Discount plan selects customers. Discount rule has all/specific products, date range, weekdays, minimum/maximum quantity, percentage/flat and active flag | Store effective plans, rules, product/customer memberships, and applied-rule snapshots. Define priority against warehouse, promotional, wholesale and manual prices, and whether coupon and plan stack | On its start/end date a rule applies only to eligible customers/products/quantities. The receipt states which rule changed the price |
| Coupon has generated/entered code, percentage/fixed amount, quantity and expiry | Unique code, validity, usage cap, per-sale redemption record and refund policy | Two checkouts competing for the last redemption produce only one redemption |
| Gift card has identifier, amount, user/customer choice and expiry. POS accepts gift card | Add issued value and immutable activity for issue, redemption, adjustment and reversal. Distinguish sale of stored value from merchandise revenue | Spending 200 from 500 leaves 300. Duplicate payment leaves 300. Expired or depleted cards are rejected |
| Customer list has deposited balance, points and discount plan. POS accepts Deposit. Reward settings expose active flag, sold amount per point and expiry duration/unit | Record deposit credits/debits per customer, and point accrual/expiry/reversal entries separately. Snapshot the active rule. Decide redemption value, earning base and expiry order from a reference fixture | Depositing 500 and applying 180 leaves 320. Applying a deposit to an invoice reduces the due without a second cash inflow. An eligible return reverses only attributable points |

### SHIP-01 · Deliveries, packing slips, challans and couriers

**Observed** — Add Delivery on an existing sale includes delivery/sale references, Packing → Delivering → Delivered choices, courier, delivered/received by, customer, attachment, address and note. Packing slips expose sale, delivery reference, product list, amount/status and Create Challan. Challans expose courier/status, order number, closing date, amount and creator/closer. A general setting controls use of packing slips.

**Proposed** — Add `Courier`, `Delivery`, `PackingSlip/Line`, `Challan/Line` linked to sales and quantities. Capture delivery address and document values as snapshots. Permit direct delivery when packing slips are disabled. When enabled, validate packing against sale quantities then group eligible unassigned slips for the selected courier into a challan. Record dispatch/close actor/date; validate before closure and prevent double inclusion. Use separate action permissions. Define partial delivery and COD reconciliation from an approved demo before adding a cash effect; delivery status alone should not generate another stock issue or payment.

**Acceptance** — Two eligible slips can appear on one challan and never on two open challans simultaneously. Delivered quantity cannot exceed sold quantity. Retrying Close preserves the closing user/date. The challan report can filter by the chosen created/closed basis.

## 6. People, HRM and tasks

### PEOPLE-01 · Commercial identities

Keep the single `people.Party` for a customer who is also a supplier. The reference supports this directly on both entry forms. Add `CustomerGroup` and a customer profile holding group, optional login link and discount-plan assignment. Derive points, deposits and due from the underlying entries. Add `Biller` with image, name, company, VAT number, address, email and phone, an optional user mapping, and explicit availability in each warehouse. A biller on a sale or quote should be a historical snapshot or a protected link. Billers are a business identity distinct from the login that recorded the sale.

Expose customer/supplier/biller lists, customer-user creation as an optional authorized action, import with duplicate review, warehouse-filtered dues and a biller option endpoint for checkout. Existing parties migrate without having to invent a group, biller or user. Choose an explicit default group for new customers. **Accept** when one dual-role party has separate receivable/payable statements, a cashier can choose only eligible billers, and a sales report still names the original biller after staff reassignment.

### HR-01 · Workforce history and attendance import

| Feature on reference | Local baseline | Implementation and acceptance |
|---|---|---|
| Promotions and designation history with effective date | `Employee.designation` and designation master | Add dated designation events, actor and reason. Show history and compute current position as of today. A future promotion cannot prematurely change the current employee card |
| Notices with title, audience and message | No HR notice model identified | Add audience/recipient resolution, publish and archive permissions. A notice for one audience stays out of other employees' feeds. Use the notification service in OPS-02 |
| Effective shifts, start/end, grace, individual break intervals and overnight checkout window | `WorkSchedule` already has weekdays, times, grace and aggregate break minutes; assignments have effective ranges | Add break intervals, early/late thresholds and device checkout window. Preserve old aggregate breaks for old schedules. Resolve each punch against the shift start date, including overnight shifts |
| Attendance Device CSV and date-format selection | Attendance API, date/check-in/check-out/status and edit permissions exist | Stage raw rows with device employee ID, local timezone, chosen date format, timestamp and fingerprint. Preview unmatched/ambiguous rows and duplicates. Apply only reviewed dates. Preserve manual edits unless a supervisor resolves the conflict |
| Global weekly holidays and public holidays | Local per-schedule working days plus global/warehouse holidays | Add an effective default calendar and define precedence with branch holidays and employee schedules. A policy edit should affect future calculation as configured without rewriting old attendance |

**Fixture** — A 22:00–06:00 worker punches out next day within the configured window. Re-importing the same file yields one attendance row for the shift start date. An unmatched device ID creates a review item and no employee attendance.

### HR-02 · Leave balances, carryover and payroll policy

**Observed** — Leave types include paid flag, annual quota and carry-forward limit. Leave Balances says current-year quotas come from the scheduler or an administrator, missing earlier balances are not inferred, and unused carry expires **after March 31**. Approvals must be chronological within a leave type and occur before payroll finalization. Late Policy includes default check-in/out, grace, monthly late allowance, late marks per leave day and early-leave threshold. The first generated payslip freezes the month's calendar/policy; existing legacy slips for a month must agree before another is generated. Generation does not pay salary, and the reference warns against regenerating earlier months. [Leave balances](https://saleerp.emanageritpark.com/hrm/leave-balances) · [Salary generation](https://saleerp.emanageritpark.com/hrm/payroll/salary-generate)

**Local** — `LeaveType.is_paid`, `LeaveRequest` and overlap/self-review protections exist. Payroll already uses effective salary structures, unpaid working days, holidays and attendance warnings. It stores slip inputs and rejects review/approval when input digests change. These solve different problems from an annual leave balance or a policy shared by every generated slip in a month.

1. Add effective leave policy and a unique balance per employee/type/year with auditable allocation, usage, carry and expiry entries. Provide admin and scheduled allocation using the same idempotent command. Treat an unknown previous year explicitly. Keep existing initialized quotas when the default changes.
2. Extend `review_leave_request`. Lock the employee/type balance, enforce chronological review, calculate chargeable working days and check payroll month closure. Split cross-year requests by applicable year. Proposed consumption order is valid carry first, then current allocation. Record the allocation breakdown so reversal restores only what was used. Confirm partial-day and insufficient-balance treatment.
3. Add a shared `PayrollMonthPolicy` snapshot with working dates/calendar scope, effective shifts or immutable references, default grace, late allowance/ratio, early threshold and calculation version. Freeze on the first committed calculation for that month under a lock. Keep draft preview side-effect free. Reuse the snapshot for every permitted recalculation and employee/currency run sharing that policy. Historical corrections should use the project's correction flow.
4. Implement attendance deductions only from an approved policy formula. Count eligible late marks after grace and allowance, apply the policy ratio, and record dates/derived days. Define interactions among lateness, unpaid leave, absence and early checkout so one working day cannot be deducted twice. Keep calendar-day salary proration separate from scheduled-working-day leave eligibility.

**Acceptance** — Running annual allocation twice yields one balance. A March 31 absence can consume eligible carried leave; an April 1 absence cannot. Two reviewers cannot overspend one balance. Changing working days after generation leaves a prior slip unchanged. A conflicting legacy snapshot blocks generation with an actionable reason.

### HR-03 · Salary advances and manual payroll

**Observed** — Salary profiles and increments are effective-dated. Salary Advances has requested/approved amounts, approval/rejection, installment count, repayment start, and Mark Paid. The page explicitly distinguishes this marker from making a payment. The separate manual payroll form records employee, date, account, amount, method and note.

**Local** — `EmployeeCompensation` already supports salary history. `EmployeeLoan`, `LoanInstallment`, payslip deductions and `SalaryPayment` already support recovery and payment outcomes. Use the compensation model for the increment screen. Add an advance request/approval/disbursement record upstream of the existing loan schedule. Link exactly one loan to the approved disbursed advance. A mark-paid confirmation can record external evidence; an actual local cash disbursement requires a separate authorized posting. For legacy manual payroll add a distinct manual voucher linked to employee, account and period, with source/reference and optional reconciliation to a slip. Surface both on a report with a duplicate-payment check.

**Acceptance** — An approved but undisbursed advance causes no deduction. A disbursed 3,000 advance scheduled in three equal installments deducts 1,000 starting in the chosen month. Retry never schedules a second loan. Payment of a generated slip and manual payment for the same liability require explicit reconciliation. Reuse run review, approval and payment resolution.

### TASK-01 · Tasks, responsibilities and groups

**Observed** — All Tasks and Responsibility show total, assigned, created, responsibility, not seen and today's completed counters, search and status filters. Task creation has title, status, assignee, group, due date and description. Task detail offers status update and subtasks with assignee, due date and status. Groups show creator and task count. Task Report shows counters and rows.

**Proposed** — Add a `tasks` Django app with `TaskGroup`, `Task`, `Subtask` and recipient-specific `TaskSeen`. Record creator, assigned user, group, due date, completed timestamp and update actor. Expose list, responsibility, group, detail/status, subtask and report views. Define whether Responsibility means assignee-only or delegated responsibility after verifying with two distinct roles; the admin alone cannot establish this. Apply consistent visibility to every counter. Record first-seen only for the viewer. Decide whether outstanding subtasks block closing a parent.

**Acceptance** — A sees a task assigned by B, B sees it in Created, an unrelated user sees only authorized tasks. Opening a task changes that recipient's unseen count once. Reassigning and completing it updates the filtered lists and date-scoped counters consistently.

## 7. Accounting, analytics and dashboard

### FIN-02 · Complete the account reports

The reference's screen labelled Balance Sheet is a per-account table of **Name, Account No, Credit, Debit and Balance**. The local direction-based ledger already holds the information for receipts and payments. Add a unique display account number, a scoped account aggregation, and an as-of date or clearly labelled current view. Define whether the opening balance appears in inflows or a distinct opening column. Reuse the existing money and fund transfer types. A transfer affects both individual account statements and nets to zero at company level. [Balance sheet](https://saleerp.emanageritpark.com/balancesheet)

Add the reference's date/account-filtered Income & Expense Report from category and posting sources; include notes/reference on local income and expense records. Reconcile the existing Daybook, account-specific statement, dated ledger and cash-flow page against the same date basis. Implement the old and new reference income/expense menu paths as shortcuts to one local workflow. **Accept** when opening 500, income 250 and expense 90 give a closing balance of 660, and transferring 60 between two included accounts leaves the combined balance at 660.

### RPT-01 · Build the analytical report catalog

Use a common report filter/service for HTML, JSON, CSV and print. Define the time zone, inclusive business dates, warehouse scope, posted versus draft populations, return dates, FX and rounding at the service boundary. Aggregate document lines, returns and payments independently before joining; a two-line sale with three payments must contribute its sale amount once. Keep unallocated global accounts visible only according to the existing access rules.

| Reference report/surface | Data and expected output | Local starting point / acceptance |
|---|---|---|
| Summary Report, profit/loss, net profit/loss | Purchases, sales, both return types, product cost, payments sent/received, income, expenses, payroll and cash in hand; company and branch breakdowns | Use posted movement costs and the ledger. The reference displays multiple formulas: one profit block uses sale less product cost, another includes both return types, and branch blocks also show sale less purchases. Reproduce these as separately named measures and verify with a fixture before signing off |
| Best Seller; dashboard monthly and yearly best sellers | Rank by net quantity and sales value with the time window visible. Stable tie-break by SKU. Reference dashboard displays top five | Sale/return lines. Test that a high-priced low-quantity product can rank differently in the two tables |
| Product Report; category stock worth; warehouse stock chart | Product/category, purchased and sold quantity/value, both returns, profit, in-stock and worth at selling price/cost; branch item count and stock quantity | `Stock` and `warehouse/activity.py` plus INV-01. Reconcile chart to product rows; label historical versus current valuation |
| Daily Sale, Monthly Sale, Daily Purchase, Monthly Purchase | Warehouse selector, period navigation, daily calendar or 12 monthly buckets with totals | Order/receipt facts per agreed recognition date. Monthly total equals sum of daily buckets including zero-activity days |
| Sale Report, Sale Report Chart, Purchase Report | Date/warehouse filtering. Purchase report exposes product, category, purchased amount, quantity, on-hand | Reuse one aggregation per metric; chart points must sum to table totals. Exclude drafts unless explicitly selected |
| Payment Report | Payments by date, reference, source, account and method as available | Use posted allocations and returns. Exclude internal transfers from customer collections and group cash/card separately |
| Customer Report, Customer Group Report, Supplier Report | Select a party or commercial group; show relevant documents, returns, payments and totals | `people.Party`, FIN-01 and PEOPLE-01. A dual-role party's supplier payments must not increase customer collections |
| Customer Due Report, Supplier Due Report | Reference, date, counterparty, grand total, returned, paid and due | FIN-01. A fully paid document with a cash refund cannot reappear as overdue. Define if the date filter means original document range or balance as of date |
| Warehouse Report | Sales, purchases, returns, income/expenses, quotations and transfers by branch | Existing warehouse activity endpoints are a substantial match. Extend missing metrics and compare access and date filters before adding another report page |
| Challan Report | Created/closed date basis, date range and amounts | SHIP-01. A late-closed challan appears in the correct period for each filter |
| Product Expiry, Product Quantity Alert, Daily Sale Objective | Lot expiry and horizon, warehouse threshold, product objective versus actual for date | INV-02. Cover boundary date, exact threshold, zero sales, returns and missing targets. Verify target counting against a live example |
| User Report, Biller Report | User or biller selection and attributed transactions | Use explicit actor/biller snapshots. One user's filtered result must never expose another branch outside their permissions |
| Accounting Income & Expense, Daybook, Cash Flow, account statement/ledger, balance view | Monetary flows, opening/closing balances, dated source rows | FIN-02 and existing `accounting` views. Distinguish trading revenue from cash received on credit |
| Task Report, payroll report and payslips | Task counts/details; payroll period, employee gross/deductions/net/settlement | TASK-01, existing `hrm/payroll_reports.py` and payslip PDF. Preserve payroll-specific permissions |

**Metric sign-off example.** Sell two units for 100 each, with posting-time cost 60 each and no tax. Return one for 100 and restore its original 60 cost. Net sales = 100, net cost of goods sold = 60, gross margin = 40. With operating expense 10 and other income 5, proposed operating result = 35. Cash can still be zero if the sale was on credit. Capture this dataset plus a tax/shipping/purchase-return example in the reference sandbox. The site's summary formula visibly subtracts shipping and tax and adds purchase returns in one block. Decide explicitly whether to publish a reference-compatible metric, a cost-based metric, or both under distinct names. The browser did not establish the underlying tax/cost sourcing.

### DASH-01 · Replace profile-only overview with operating metrics

Extend `DashboardPageView` with authorized period and warehouse filters (Today, Last 7 Days, This Month, This Year), KPI cards, cash-flow chart, yearly report, quick actions, latest five sales/purchases/quotations/payments and top-five products. Keep profile details on the existing profile page. Load all widgets from RPT-01 services and omit unauthorized modules. Define Revenue and Sale Amount separately before returning both fields. **Accept** when the dashboard, report and underlying document list agree for the same period/branch, including local-midnight transactions and an empty period.

## 8. Administration and operational features

| ID | Reference coverage | Proposed change and sign-off |
|---|---|---|
| OPS-01 | General Setting, POS Settings, HRM Setting, roles, profile | Store typed settings with defaults, validation, access, change history and business-effective date where needed. Configure title/logo/company/VAT, timezone, RTL, currency/position/decimals, date/invoice format, staff access, expiry horizon, selling/quoting without stock, packing slips, manufacturing flag, POS defaults and HR policy. Place each decision at the server action as well as its form. Define the experience when negative stock is allowed without breaking existing stock constraints. Test disabled flags on a direct API call |
| OPS-02 | Notifications list/send, bell, SMS templates, Create SMS, after-sale SMS, Mail/SMS settings | Add templates, authorized audiences, notification records with read timestamps, and an outbound queue with source-event idempotency/retry. Use the existing account mail configuration as a starting point. Keep transactional document messages separate from login messages. Tests use a fake transport and prove a retried sale sends one notification |
| OPS-03 | Custom Field List | Typed definitions and values for Product, Sale, Purchase and Customer. Observed types are text, number, textarea, select, multi-select, checkbox, radio and date. Store defaults/options and table/invoice/required/admin-only/disabled flags. Apply rules to imports, APIs, local forms and document snapshots. A required field applies to new records without invalidating historic invoices |
| OPS-04 | POS tables and settings; barcode settings; language management | Add table master/assignment only when enabled. Store named sticker/sheet templates and thermal widths. Use localizable labels/formatting. The reference Languages page returned a server error during review, so translation editing/import behavior needs separate evidence |
| OPS-05 | Payment Gateways | Settings form showed PayPal, Stripe, Razorpay, Paystack, Mollie, Xendit, bKash, SSLCommerz, M-Pesa and Pesapal. Configuration visibility does not verify working payments. Implement only enabled providers through one payment-attempt interface, with provider references, pending/succeeded/failed outcomes, verified callbacks and retry-safe settlement. For Bangladesh rollout, bKash or SSLCommerz may be the first integration milestone; business priority is a rollout choice. Verify each provider in its own sandbox |
| OPS-06 | Audit Logs | Append scoped business events for create/update, approval, settlement/refund, stock posting, close, imports and policy changes. Store whitelisted before/after values, actor, entity, timestamp, request key and permitted client metadata; redact sensitive fields. Local `PayrollEvent` and finance histories can link by source. Test event visibility and absence of duplicate events on retry |
| OPS-07 | Backup Database, role permissions, profile | Roles, warehouse access and profile already have local UI. Verify per-action permissions for new features. The reference offers Backup Database; the review did not trigger it. For equivalent operations, define who can start a backup, retained contents, storage, audit trail and restore procedure. Verify on an isolated copy before exposing a backup control |
| OPS-08 | CSV imports and list exports | Dedicated imports observed for purchase, sale and transfer, plus master-data import actions including warehouse, products, categories, brands, units, tax, customer groups, parties, billers and expense categories. Device attendance uses its own parser. Add downloadable templates, row validation, dry-run preview, stable external key, commit result and row-level errors. Reuse existing posting commands; group transaction lines by external document reference. Implement lists' search/date/warehouse filters, printable/exportable selection and server-scoped export. A rejected row cannot leave a half-posted order, and a repeated file cannot double stock |

## 9. Proposed API and page handoff

**Reference CSV contracts.** The on-screen sale, purchase and transfer import instructions give the following column order. The warehouse, party, status and other header values are chosen on the form. Treat one file plus its form as one document in the parity importer. If a separate future bulk importer supports multiple documents, require an explicit external document key.

| Import | Reference columns in order | Implementation detail |
|---|---|---|
| Sale | `product_code, quantity, sale_unit_code, price, discount_per_unit, tax_name` | All columns required. The form specifies `n/a` for a digital product's sale unit. Multiply the per-unit discount by quantity when converting to the local line-level discount amount |
| Purchase | `product_code, quantity, purchase_unit_code, cost, discount_per_unit, tax_name` | Resolve the purchase unit and named tax. Snapshot resolved values before final review and apply the same validations as manual entry |
| Transfer | `product_code, quantity, product_unit, product_cost, tax_name` | Validate branch access for both ends. A submitted Pending transfer produces the same state as manual entry |

Other verified UI details for implementation include the label printer's selectable **Product Name, Price, Promotional Price and Business Name**, each with a size control, and the sale action menu's **Add Return, Generate Invoice, Send SMS, WhatsApp Notification and Add Delivery**. For WhatsApp, determine whether the reference opens a prefilled client message or uses a provider before implementing delivery tracking. No outbound message was sent during this review.

The following are **new** local routes to design. Existing mounted routes and actions in §3 remain the integration contract. A POST command with financial or inventory effects should return its stable document/event ID and resulting state. Use the project's existing DRF validation, permission and conflict response conventions.

| Feature | Suggested endpoint or command | Main inputs and output |
|---|---|---|
| Partial receipt | `POST /purchase/api/purchase-orders/{id}/receipts/` | Key, business date, order line, quantity/unit, lot/serial → receipt ID, remaining quantities, receipt state |
| Collect / pay supplier | Extend the existing sales-order payment collection with POST; add `POST /accounting/api/purchase-orders/{id}/payments/` | Key, method, account, amount, currency context, reference and date → payment ID, allocations, updated paid/returned/due |
| POS | `POST /sale/api/registers/open/`, `POST /sale/api/pos/checkout/`, `POST /sale/api/registers/{id}/close/` | Register, warehouse, biller, customer, document, lines, tenders and key → invoice, receipt and drawer impact; counted cash on close |
| Count / staged transfer | `POST /warehouse/api/counts/{id}/finalize/`; dispatch and receive actions on a transfer | Count snapshot/actuals or transfer lines and key → variance movements or dispatch/receipt event |
| Manufacturing | Material/BOM CRUD under `/manufacturing/api/`; BOM approve, production preview, draft and post actions | BOM version, quantities, warehouses, actual use, labor, overhead → per-component availability/shortage, unit cost and posted output |
| Quote → purchase | `POST /sale/api/quotations/{id}/convert-purchase/` | Supplier, selected revision/cost basis and key → draft PO and source link |
| Fulfillment | Delivery create/status, packing-slip create, challan issue/close under `/sale/api/` | Sale line quantities, courier, identifiers, destination and dates → linked documents and printable output |
| Payroll/leave | Annual allocation, month policy read/freeze with calculation, device preview/commit and advance actions under `/hrm/api/` | Policy/period, employees, paid status or punch batch → allocation, validation report or auditable workflow result |
| Tasks / insights | `/tasks/api/` for task/group/subtask actions; `GET /report/api/analytics/{report}/` and dashboard summary | Date, warehouse and resource filters → scoped rows, units, totals and metric definitions |

**Illustrative checkout payload** for a 1,000 BDT sale. IDs are synthetic. Resolve pricing and total on the server. A genuine request key must be unique to this attempted checkout.

```json
{
  "request_key": "137f2e9d-bb3e-47e5-baa1-8199bc1750a7",
  "register": 4,
  "warehouse": 2,
  "biller": 3,
  "customer": 10,
  "currency": "BDT",
  "lines": [{"product": 25, "quantity": "2", "unit": 1}],
  "tenders": [
    {"method": "cash", "account": 7, "amount": "400.00"},
    {"method": "card", "account": 8, "amount": "600.00"}
  ]
}
```

## 10. Delivery sequence and migration

| Milestone | Implement together | Exit condition |
|---|---|---|
| 1 · Establish the baseline | Add fixtures for old sales, purchases, returns, mixed currencies, group scope and payroll. Agree on credit-sale policy, stock valuation and KPI names. Introduce audit/posting keys | Legacy automatic payment and stock behavior has a repeatable regression test and current balances reconcile |
| 2 · Credit and inventory foundations | FIN-01, INV-01, initial PEOPLE-01 billers/groups, TRADE-01 partial receipts and header pricing | Credit sale and partial receipt pass the worked examples, old callers still get automatic settlement, original balances reconcile |
| 3 · Warehouse operations | INV-02 and INV-03, OPS-08 imports for these operations, cutover of all existing stock writers | Stock, movement, lot and in-transit balances reconcile. Counting, returns and transfers work under concurrent requests |
| 4 · Selling and dispatch | QUO-01, POS-01, PRICE-01, SHIP-01, related settings, receipts and document outputs | Draft → mixed-tender checkout → delivery → partial return reconciles by drawer, account, stock and customer |
| 5 · Production | MFG-01 on costed stock and existing warehouse scope | Draft, shortage, post, retry and reporting pass the manufacturing fixture |
| 6 · Workforce and assignments | HR-01/02/03 and TASK-01. This stream can run alongside inventory once permission and audit contracts are available | Overnight import, leave expiry, month freeze, advances, scoped tasks and compensation history pass |
| 7 · Analytics and operator tools | FIN-02, RPT-01, DASH-01 and remaining OPS work. Add basic dashboards earlier as each source becomes trustworthy | Report totals reconcile to source events, documented date and valuation policies, for both admin and restricted roles |

**Deployment procedure**

1. Use additive migrations. Introduce nullable optional links and explicit defaults. Backfill supplier/customer role mappings from existing parties; leave unknown billers, historical lots and costs unassigned or flagged. Preserve all old document numbers, payment links, revisions and reports.
2. Create opening stock at a known cutoff. For an already-received purchase, record a labelled legacy receipt marker with historical line quantities where evidence supports them. Represent an already-completed transfer as received. These are metadata migrations; historical stock and cash must not be posted again. Import historical account entries as already settled according to their actual ledger rows.
3. During cutover either suspend affected stock writers briefly or capture a consistent opening snapshot and replay only events after its cutoff. Compare per-warehouse/SKU, per-account balance, open dues and employee recovery balances before enabling new writers. Keep a reconciliation report with discrepancies and resolution owner.
4. Roll out by flag/warehouse. Keep one writer for each business event. For rollback, disable new entry points and retain new events. Reverse posted business effects through compensating transactions. Restore a backup only under an explicit operational recovery procedure.
5. Keep data scope consistent in APIs, HTML, bulk actions, autocomplete, PDF and exports. For transaction retries compare the key **and payload**. Add constraints for origins, amounts, valid states, stock availability as applicable and unique event allocation.

## 11. Release acceptance and remaining verification

**Baseline verification on the reviewed checkout.** `manage.py check` passed. The Django test runner reported **718 tests, two failures and one import error**. The HR payslip and issued-quotation PDF checks received HTTP 503 where they expected 200; WeasyPrint reported that the native `libgobject-2.0-0` library could not load. The shared report test module could not import `pypdf`. The project documents the Windows rendering dependency in `report/README.md` and lists the PDF test requirement in `report/requirements-test.txt`. Verify the native runtime and test dependency in the intended environment and rerun the full suite before treating PDF output as release-ready. This review changed only this Markdown file.

**Where to put the regression tests**

| Work package | Extend existing tests | Regression to guard |
|---|---|---|
| FIN-01 and TRADE-01 | `accounting/test_finance.py`, `purchase/tests/test_receiving.py`, `sale/tests/test_completion.py` and the corresponding concurrency suites | Existing automatic settlements still create exactly one payment using the stored FX rate. New explicit credit terms post stock without a collection. A failed posting rolls back stock and cash together where both were requested |
| Returns | `sale/tests/test_sales_returns.py`, `purchase/tests/test_purchase_returns.py` | Cap cumulative quantities and refunds. Verify unpaid, partially paid and fully paid documents with one and multiple accounts |
| INV-01/03 | `warehouse/tests/test_transfers.py`, `warehouse/tests/test_transfer_concurrency.py`, `warehouse/tests/test_manual_stock_removed.py` | Preserve access to a transfer from either end for reads and existing stricter write scope. Add count/adjustment commands without introducing a general-purpose stock edit endpoint |
| QUO-01 | `sale/tests/test_quotations.py` | Sales conversion still occurs once and issue freezes the document. New purchase conversion enforces its own authorization and cost selection |
| HR-01/02/03 | `hrm/tests/test_leave_workflow.py`, `hrm/tests/test_attendance_api.py`, `hrm/tests/test_payroll_calculation.py` | Approved leave, effective compensation and previous payslips stay reproducible at cutoff and retry boundaries |
| Reporting | `warehouse/tests/test_activity_report.py`, `report/tests/test_reports.py`, `accounting/test_finance.py` | Totals reconcile across pagination and export; PDFs honor permissions; a branch filter cannot reveal another branch's counts or value |

| Scenario | Required evidence |
|---|---|
| Legacy order received/completed before upgrade | Same totals and balances after migration; opening movements and historical markers cannot be run twice |
| Partial supplier receipt and independent payment | Receive 4/10, pay part, receive 6/10, return 2, reconcile vendor due and warehouse stock without a second full payment |
| Discounted quote to purchase and sale | Original issued snapshot stays intact, supplier purchase uses reviewed costs, retries return the existing order, and permissions cover each target app |
| Checkout race | Two sessions attempt the last tracked unit; a single sale succeeds under the default no-negative-stock policy, with no orphan payment or coupon redemption |
| Production shortage after preview | Another order consumes input before posting. Commit reports shortage; no finished stock, labor cost, cash or supplier payable is created |
| Staff and data visibility | Branch-limited cashier sees only permitted products, documents, exports and totals. HR reviewer cannot approve own leave; payroll inputs remain private |
| Report reconciliation | Net sales, returns, cost, margin, cash and outstanding are independently reproducible from source events for a fixture with FX, split tenders, shipping and returns |
| HR period boundaries | Overnight shift, March carry expiry, mid-month raise, already-finalized payroll and advance retry produce stable slips and histories |
| Operational failure | Repeated import, receipt reprint, outbound retry and duplicated provider callback each have at most one business effect |

**Reference behavior still requiring a controlled fixture**

| Question | How to resolve before calling it parity |
|---|---|
| Taxes, order-discount allocation, costs, FX, shipping and returns in profit widgets | Create a known dataset on an authorized test copy. Compare invoice, product report, summary and dashboard separately; record amounts and rounding in tests |
| Raw-material stock entry, BOM Net/Gross interpretation, rounding to issue multiples, rejected-output costing, BOM approval rights | Complete a small material receipt and two production orders with different bases/waste. Compare preview, posting and resulting warehouse cost |
| POS register closure, gift-card top-ups/refunds, points redemption, deferred gateway settlement, stockless sale/quotation setting | Exercise each on an isolated register with non-live tenders and two roles. Record which actions reserve, post, refund or only change document state |
| Transfer Sent, partial delivery, packing-slip and challan closure side effects | Compare physical and in-transit stock plus ledger before dispatch, after receipt, after close and after repeated requests |
| Salary late/early deduction formula, advance rounding, employee-by-employee generation vs. local batch runs | Use an anonymized two-employee month with a holiday, late punches, leave and an advance. Compare each payslip line and payment separately |
| Task visibility, staff record scope, language administration, backup, mail/SMS and provider delivery | Verify with a restricted reference account and a staging service. The Languages page failed during inspection. Settings and buttons alone do not establish delivery, restoration or provider success |

**Primary evidence links.** Use these entry points alongside the local source paths in §3 when implementing or rechecking a requirement.

- [Dashboard](https://saleerp.emanageritpark.com/dashboard) · [Product form](https://saleerp.emanageritpark.com/products/create) · [Stock counts](https://saleerp.emanageritpark.com/stock-count) · [Transfers](https://saleerp.emanageritpark.com/transfers/create) · [Raw material balances](https://saleerp.emanageritpark.com/raw-materials/stock) · [Production orders](https://saleerp.emanageritpark.com/production-orders)
- [Sales](https://saleerp.emanageritpark.com/sales) · [Purchases](https://saleerp.emanageritpark.com/purchases) · [POS](https://saleerp.emanageritpark.com/pos) · [Register](https://saleerp.emanageritpark.com/cash-register) · [Quotations](https://saleerp.emanageritpark.com/quotations) · [Packing slips](https://saleerp.emanageritpark.com/packing-slips) · [Challans](https://saleerp.emanageritpark.com/challans)
- [Profit/loss summary](https://saleerp.emanageritpark.com/report/profit_loss) · [Customer due report](https://saleerp.emanageritpark.com/report/customer-due-report) · [Account ledger](https://saleerp.emanageritpark.com/accounts/statement) · [Leads](https://saleerp.emanageritpark.com/leads) · [Tasks](https://saleerp.emanageritpark.com/task/all)
- [Shifts](https://saleerp.emanageritpark.com/hrm/setting/shift-schedule) · [Late policy](https://saleerp.emanageritpark.com/hrm/setting/late-policy) · [Salary advances](https://saleerp.emanageritpark.com/hrm/advances) · [General settings](https://saleerp.emanageritpark.com/setting/general_setting) · [POS settings](https://saleerp.emanageritpark.com/setting/pos_setting) · [Audit logs](https://saleerp.emanageritpark.com/audit-logs)
