# heyyERP for V-Rub: decisions and inputs we need from V-Rub

For V-Rub's management, accounts, HR, production, maintenance and IT. Updated 2026-11-03.

The software for phases 0 to 11 is built and tested, and Phase 12 (reports, notifications, e-invoice, invoice printing, approvals by link, delegation, shift alerts) is under way. Where the business had not yet decided something, we
chose a **sensible default and made it a company setting**, so V-Rub's administrator can change it at any
time without a software release. This document lists every such choice, what the software does today, and
what we need from V-Rub. Nothing here blocks testing; everything here must be confirmed before go-live.

How to read the tables: **Today** is what the software does now. **Setting** is where it is changed (an
administrator changes "settings" in the admin screen; "master data" means records that V-Rub's own team
enters). **From** says who in V-Rub should answer.

**How to reply.** Please answer in the last column of each table you own (or mark "OK as proposed"). A
short reply such as "F3: lock on the 5th, production 2 extra days" is enough. Items marked **NEW** were added
on 2026-11-03. Anything left open keeps the default shown, so nothing blocks testing.

## 1. Decisions needed (what the business must choose)

### 1.1 Finance and accounts (From: Finance Controller, auditor)

| # | Decision | Today | Setting | V-Rub reply |
|---|---|---|---|---|
| F1 | Is heyyERP the books of record, or does V-Rub keep an external accounting package? | We assume heyyERP is the books of record | none yet; an export to the package is a Phase 12 item |  |
| F2 | Chart of accounts for each plant: names, codes and which account is used for what | A standard chart of 30 accounts is proposed (bank, receivables, payables, inventory by type, GST, TDS, sales, cost of sales, depreciation and so on) | Chart of accounts screen |  |
| F3 | Month-end posting lock: which day of the month, and how many days into the next month production may still post | Locked on the last day of the month; production gets 1 extra day | `lockDay`, `prodLockExtraDays` |  |
| F4 | How finished goods are valued: actual cost, or a standard cost per item | Actual (material issued plus overhead) | `costMethod` |  |
| F5 | Overhead added to the material cost of production | 0% | `overheadPct` |  |
| F6 | Machine and labour cost in production cost | On; each machine has an hourly machine rate and an hourly labour cost (both 0 until entered) | `costMachineLabour`; rates on each machine |  |
| F7 | When cost of sales is booked: at dispatch or with the invoice | At dispatch | `cogsTiming` |  |
| F8 | Should production be booked to the accounts (raw material to work in progress to finished goods)? | Yes, in plants that have a chart | `productionPosting` |  |
| F9 | How a transfer between two V-Rub plants is booked: as a sale and purchase between the plants, or a plain inventory transfer at cost | Sale and purchase, at cost | `transferScheme` |  |
| F10 | Do transfers between plants get an inter-unit invoice (separate GSTINs)? | Yes | `interUnit` |  |
| F11 | GST rate on scrap sales, and whether the entered amount already includes GST | 0% (no GST charged) | `scrapGstRate`, `scrapAmountIncludesGst` |  |
| F12 | TDS: sections and rates to use, and which vendors fall under which section | Common rates built in: 194C 2%, 194H 5%, 194I 10%, 194J 10%, 194Q 0.1%; chosen per bill | rate can be overridden per bill |  |
| F13 | Price tolerance between purchase order and supplier invoice | 0% | `priceTolPct` |  |
| F14 | Days within which MSME vendors must be paid | 45 | `msmePayDays` |  |
| F15 | Should a supplier-bill exception raised by an Accounts user be approved by a second Accounts person? | Yes, a second person must approve | approval rule R-BILL, option "skip requester role" |  |
| F16 | Credit control: only warn when a customer exceeds the credit limit, or block? Overdue invoices: warn or block? | Warn only | `creditLimitBlock` |  |
| F17 | Bank file: the exact format V-Rub's bank accepts for bulk payments | A generic CSV | needs the bank's format sheet |  |
| F18 | Custody of the encryption key that protects vendor bank account numbers | One key in the server settings | `DATA_ENCRYPTION_KEY`; V-Rub must decide who keeps the backup |  |

### 1.2 Fixed assets (From: Finance Controller, auditor)

| # | Decision | Today | Setting | V-Rub reply |
|---|---|---|---|---|
| A1 | Useful life, method (straight line or written down) and rate for each asset class (Companies Act book) | Entered per asset; nothing assumed | asset register |  |
| A2 | Income-tax block rates per asset class | Entered per asset | asset register |  |
| A3 | Residual value limit | At most 5% of cost | `salvageMaxPct` |  |
| A4 | From when an asset is depreciated: the day it is put to use, the whole month, or the next month | From the day put to use (by days) | `depreciationConvention` |  |
| A5 | Half-rate in the first year for assets used fewer than 180 days (Income-tax book) | 180 days; 0 switches it off | `itHalfRateDays` |  |
| A6 | If depreciation is run after the month is locked: refuse, or book it in the current month? | Refuse | `depreciationLateBooking` |  |
| A7 | How an asset purchase is booked to the "fixed assets" account (through the supplier bill or a voucher) | Registering an asset does not post; the purchase must be booked separately | needs the accountants' procedure |  |

### 1.3 HR and attendance (From: HR head)

| # | Decision | Today | Setting | V-Rub reply |
|---|---|---|---|---|
| H1 | Leave types and yearly days | Casual 7, Sick 7, Earned 15, plus unpaid (the BRD's demo values) | leave types screen |  |
| H2 | Leave year: January to December or April to March | January | `leaveYearStartMonth` |  |
| H3 | Do Sundays count as leave days? Do company holidays count? | Neither counts | `leaveCountSundays`, `leaveHolidaysCount` |  |
| H4 | Is leave given for the whole year at the start, or earned monthly (can differ per leave type)? | The whole year at the start | `leaveAccrual`, or per leave type |  |
| H5 | When a department head applies for leave, who approves? | The department head's own step is skipped | approval rule R-LEAVE |  |
| H6 | Holiday list for each year (all plants, or per plant) | Empty | holiday calendar screen |  |
| H7 | Shifts: names, start and end times, grace minutes, per plant | None entered | shift master screen |  |
| H8 | Appraisal: yearly or half yearly, the score scale, who reviews | Yearly; score 0 to 5; employee, then manager, then department head, then HR | `appraisalMaxScore`; approval rule R-REV |  |
| H9 | Biometric attendance devices: make, model and the file or API they produce | A generic import (employee number, check-in, check-out) | needs a sample file |  |
| H10 | Payroll and recruitment: should they be built (BRD phase 2)? If yes: salary structure, PF, ESI, professional tax, TDS on salary, pay cycle | Not built | needs a go-ahead and the statutory details |  |

### 1.4 Maintenance and production (From: Maintenance head, Production head)

| # | Decision | Today | Setting | V-Rub reply |
|---|---|---|---|---|
| M1 | Preventive maintenance plans: interval in days or shots for every machine and mould | Entered per plan | maintenance plans screen |  |
| M2 | Shot limit (life) for every mould | Entered per mould | mould master |  |
| M3 | How soon before a maintenance falls due should it show as "due soon" | 7 days | `pmDueSoonDays` |  |
| M4 | MTBF basis: calendar hours, or only scheduled working hours | Calendar hours | needs a decision; code change if scheduled hours |  |
| M5 | Spare parts: the list of spares, their stores and reorder levels | Spares are ordinary items of type "Spare" | item master |  |
| M6 | Excess receipt allowed over a purchase order | 10% | `grnTolPct` |  |
| M7 | Which lot is issued first (earliest expiry or oldest) | Earliest expiry | `issueRule` |  |
| M8 | Negative stock allowed? | No | `negativeStock` |  |

### 1.5 Kaizen (From: Plant Head, HR)

| # | Decision | Today | Setting | V-Rub reply |
|---|---|---|---|---|
| K1 | Score scale for evaluating a kaizen | 0 to 10 | `kaizenMaxScore` |  |
| K2 | Reward scheme: a percentage of the verified saving, and a ceiling per kaizen | No reward scheme (0%) | `kaizenRewardPct`, `kaizenRewardMax` |  |
| K3 | Who evaluates a kaizen | Department head, then Plant Head | approval rule R-KZ |  |

### 1.6 Purchasing and sales limits (BRD rules V-Rub had not yet answered)

These are in the BRD marked "awaiting decision". They are not enforced until V-Rub gives a value.

| # | Decision | Today | Setting | V-Rub reply |
|---|---|---|---|---|
| P1 | Cash purchase limit per bill | No limit | `cashLimit` |  |
| P2 | Minimum quotations before a purchase order | Not enforced | `minQuotes` |  |
| P3 | Quote margin above which the Managing Director must approve | Not enforced | `marginMD` |  |
| P4 | Approval routes and value bands for purchase orders, indents, bills, scrap, counts, leave, kaizen, reviews, bills of material, vendors | The BRD's matrix is loaded | approval rules screen |  |

### 1.7 E-invoice and e-way bill (NEW; From: Finance Controller, CA, Sales head)

| # | Decision | Today | Setting | V-Rub reply |
|---|---|---|---|---|
| E1 | Which plants and customers need an e-invoice (it depends on turnover and registration)? Raised by a person on request, or automatically when the invoice is made? | Off. Customers without a GSTIN never get one | `eInvoice` (OFF, MANUAL, AUTO) |  |
| E2 | Consignment value above which an e-way bill is expected (some states differ) | 50,000 | `ewbThreshold` |  |
| E3 | Do transfers between plants with different GSTINs need an e-invoice and e-way bill? | Not generated (numbers are entered by hand) | needs your answer; a small addition |  |
| E4 | Do you issue credit notes or debit notes to customers (returns, rate differences)? If yes, who approves them? | The system has no credit note yet | needs your answer; a new document |  |
| E5 | How many times should the system retry the GST portal before giving up and alerting the administrator? | 5 tries, with growing waits | `gspMaxAttempts` |  |
| E6 | Address and six-digit pincode of every plant and every customer (the portal refuses an e-invoice without them) | Empty | master data (plant and customer address) |  |

### 1.8 Printed tax invoice (NEW; From: Finance Controller, Sales head)

| # | Decision | Today | Setting | V-Rub reply |
|---|---|---|---|---|
| T1 | What should the invoice show for each plant: legal name, bank details, terms and conditions, authorised signatory, footer line. Please also send a sample of your current invoice | Blank until entered | print profile of each plant (entered by your administrator) |  |
| T2 | One copy, or three (Original for recipient, Duplicate for transporter, Triplicate for supplier) | One | `invoiceCopies` |  |
| T3 | Anything else that must appear (logo, PAN, CIN, MSME number, declaration, place of supply, reverse charge) | Not printed | needs your list; small additions |  |

### 1.9 Approvals, delegation and alerts (NEW; From: MD, Plant Head, HR head, Production head)

| # | Decision | Today | Setting | V-Rub reply |
|---|---|---|---|---|
| W1 | Delegation: may any approver hand their approvals to someone for a period? Longest period? Who may be the stand-in? | Anyone may delegate their own role's approvals to any active user, up to 30 days; the stand-in cannot approve what the delegator submitted | `delegationMaxDays` |  |
| W2 | Approving or rejecting from the link in an e-mail or WhatsApp message (no sign-in) - do you accept this? | Off. When on: the link works once, for the person and the step it was sent to, for 48 hours, up to 50,000 | `approvalByLink`, `approvalLinkHours`, `approvalLinkMaxAmount` |  |
| W3 | Administrator, MD and Accounts roles need a second factor to sign in. We therefore never send them links. Agree? | Never send | fixed by the security policy; change needs your decision |  |
| W4 | Alert "shift ended without a production entry": who should receive it (the "line supervisor"), and how many minutes after the shift end should it be sent? | Everyone who plans production in that plant; 30 minutes after the shift end and its grace time; in the app and on WhatsApp; only for machines that ran in the previous 7 days; not on holidays | `shiftAlertAfterMinutes` (0 switches it off); shifts and holidays are the ones your HR enters |  |
| W5 | Which messages go by e-mail and which by WhatsApp | First reminder: app and e-mail. Second reminder: app and WhatsApp. Overdue: all three | `notifyEmail`, `notifyWhatsapp` |  |
| W6 | How often a failed message is retried before it is marked failed | 5 times | `notifyMaxAttempts` |  |

## 2. Information and master data V-Rub must supply

### 2.1 For go-live (From: Finance, Stores, Sales, HR, IT)

| # | What | Why | V-Rub reply |
|---|---|---|---|
| D1 | The plants: GSTIN, state, full address with pincode, and which are separate GST registrations | GST returns, e-invoice, printed invoice, inter-unit invoices |  |
| D2 | Departments with their head role; the list of users with role and plant access | Approvals and access |  |
| D3 | Items: code, description, type (raw material, work in progress, finished goods, spare, consumable, capital), unit, HSN, shelf life, reorder level | Everything |  |
| D4 | Customers with GSTIN, full address with pincode, payment terms, credit limits, customer part numbers | Sales, credit control, e-invoice |  |
| D5 | Vendors with GSTIN, PAN, MSME status, payment terms, bank details | Purchasing, payments, TDS |  |
| D6 | Tax codes (GST rates) in use | Orders, invoices, bills |  |
| D7 | Machines and moulds with hourly rates and shot limits | Costing, maintenance |  |
| D8 | Compound recipes, bills of material and production routes | Production |  |
| D9 | Employees: number, name, plant, department, manager, joining date | HR, approvals |  |
| D10 | Opening position at the cut-over date: stock by lot with cost, open purchase orders, open sales orders, vendor and customer balances, asset register, leave balances, trial balance | Starting the books and stock correctly |  |
| D11 | Standard costs per item (only if V-Rub chooses standard costing) | Costing |  |

### 2.2 For Phase 12: reports, integrations, AI and release (From: IT, management)

| # | What | Why | V-Rub reply |
|---|---|---|---|
| R1 | The list of reports V-Rub needs first, with layout samples and who sees which | Reports and exports |  |
| R2 | Export formats needed (Excel, PDF, both). Today all three of CSV, Excel and PDF (with Hindi) are available | Reports |  |
| I1 | Which GST service provider (GSP) V-Rub has signed or will sign with; the provider's API document, a sandbox account first and then production credentials. The system is ready on our side; only the provider's exact request paths need to be set once we have this | Generating IRN and e-way bills automatically |  |
| I2 | A sending e-mail domain and its DNS access; sender address | Notifications and account verification mail |  |
| I3 | WhatsApp provider and approved message templates (if WhatsApp alerts are wanted) | Notifications |  |
| I4 | Shared mailbox for customer schedules (if the mailbox watcher is wanted), the customers who send schedules, and a sample e-mail from each | Importing delivery schedules (not built yet) |  |
| L1 | Barcode labels: what goes on a label (item, lot, quantity, date), label size, barcode type (Code 128, QR), the label printer make and model (Zebra/ZPL or a normal printer) | Barcode labels (not built yet) |  |
| I5 | Which accounting package and what format (only if V-Rub keeps one) | Accounting integration |  |
| I6 | AI: which assistants V-Rub wants (for example supplier-bill reading, questions about data), the approved AI provider and budget | AI features |  |
| S1 | Where the system will run: server or cloud, who administers it, domain name and security certificate | Deployment |  |
| S2 | Backup and recovery targets (how much data loss and downtime is acceptable) and retention period | Backups and rollback |  |
| S3 | Security policy: password rules, two-factor sign-in for which roles, any IP restrictions | Security |  |
| S4 | Who will do user acceptance testing, and the go-live date | Release |  |
| S5 | A WhatsApp Business account and an approved message template named for alerts (the system sends the title and the text as two fields) | WhatsApp alerts and links |  |

## 3. Things V-Rub's team must do before go-live

1. Review the standard chart of accounts and the approval routes with the accountants and sign them off.
2. Enter or approve the master data above, and the opening balances.
3. Choose the settings in section 1 (our defaults are listed so nothing is missing if a question is left open).
4. Run user acceptance tests with real documents for each department.
5. Keep the encryption key and the database backups in a place the company controls.

## 4. How decisions are recorded

Every setting change is saved with who changed it and when, and the previous value is kept. An
administrator can see the history of any setting. A change applies to documents made after it; documents
already made keep what they had. Approval routes work the same way: a change applies only to documents
submitted afterwards.

Questions about this document go to the backend owner (Deepak).
