-- heyyERP for V-Rub Suspensions
-- PostgreSQL 16 logical schema blueprint, based on BRD v1.0 and prototype v4.
-- Review tenant/plant setup, numbering, tax and approval policy before production use.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS erp;
SET search_path = erp, public;

-- Common tenancy, organization, identity and authorization
CREATE TABLE tenant (
    tenant_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','SUSPENDED','CLOSED')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE unit (
    unit_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    code text NOT NULL,
    name text NOT NULL,
    gstin varchar(15),
    state_code varchar(2),
    active boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, unit_id),
    UNIQUE (tenant_id, code),
    UNIQUE (tenant_id, gstin)
);

CREATE TABLE department (
    department_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid,
    parent_department_id uuid,
    code text NOT NULL,
    name text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, department_id),
    UNIQUE (tenant_id, code),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, parent_department_id) REFERENCES department(tenant_id, department_id)
);

CREATE TABLE store (
    store_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    store_type text NOT NULL CHECK (store_type IN ('RAW_MATERIAL','WIP','FINISHED_GOODS','SPARES','OTHER')),
    active boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, store_id),
    UNIQUE (unit_id, code),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id)
);

CREATE TABLE role (
    role_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    code text NOT NULL,
    name text NOT NULL,
    is_system boolean NOT NULL DEFAULT false,
    active boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, role_id),
    UNIQUE (tenant_id, code)
);

CREATE TABLE permission (
    permission_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL UNIQUE,
    description text NOT NULL
);

CREATE TABLE role_permission (
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    role_id uuid NOT NULL,
    permission_id uuid NOT NULL REFERENCES permission(permission_id),
    PRIMARY KEY (role_id, permission_id),
    FOREIGN KEY (tenant_id, role_id) REFERENCES role(tenant_id, role_id)
);

CREATE TABLE role_module (
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    role_id uuid NOT NULL,
    module_code text NOT NULL,
    PRIMARY KEY (role_id, module_code),
    FOREIGN KEY (tenant_id, role_id) REFERENCES role(tenant_id, role_id)
);

CREATE TABLE app_user (
    user_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    auth_subject text NOT NULL,
    role_id uuid NOT NULL,
    department_id uuid,
    display_name text NOT NULL,
    email text,
    phone text,
    status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','DEACTIVATED','LOCKED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, user_id),
    UNIQUE (tenant_id, auth_subject),
    FOREIGN KEY (tenant_id, role_id) REFERENCES role(tenant_id, role_id),
    FOREIGN KEY (tenant_id, department_id) REFERENCES department(tenant_id, department_id)
);

CREATE TABLE user_unit_scope (
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    user_id uuid NOT NULL,
    unit_id uuid NOT NULL,
    PRIMARY KEY (user_id, unit_id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES app_user(tenant_id, user_id),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id)
);

CREATE TABLE business_setting (
    setting_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    setting_key text NOT NULL,
    setting_value jsonb NOT NULL,
    effective_from timestamptz NOT NULL DEFAULT now(),
    effective_to timestamptz,
    changed_by uuid,
    CHECK (effective_to IS NULL OR effective_to > effective_from),
    FOREIGN KEY (tenant_id, changed_by) REFERENCES app_user(tenant_id, user_id),
    UNIQUE (tenant_id, setting_key, effective_from)
);

CREATE TABLE audit_log (
    audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    actor_user_id uuid,
    actor_role_code text,
    action text NOT NULL,
    entity_type text,
    entity_id text,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    FOREIGN KEY (tenant_id, actor_user_id) REFERENCES app_user(tenant_id, user_id)
);

-- Shared masters
CREATE TABLE uom (
    uom_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    decimal_places smallint NOT NULL DEFAULT 3 CHECK (decimal_places BETWEEN 0 AND 6)
);

CREATE TABLE item_category (
    category_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    parent_category_id uuid,
    code text NOT NULL,
    name text NOT NULL,
    UNIQUE (tenant_id, category_id),
    UNIQUE (tenant_id, code),
    FOREIGN KEY (tenant_id, parent_category_id) REFERENCES item_category(tenant_id, category_id)
);

CREATE TABLE item (
    item_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    category_id uuid,
    uom_id uuid NOT NULL REFERENCES uom(uom_id),
    code text NOT NULL,
    name text NOT NULL,
    item_type text NOT NULL CHECK (item_type IN ('RM','WIP','FG','SPARE','CONSUMABLE','CAPITAL','OTHER')),
    hsn_code text,
    shelf_life_days integer CHECK (shelf_life_days IS NULL OR shelf_life_days >= 0),
    active boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, item_id),
    UNIQUE (tenant_id, code),
    FOREIGN KEY (tenant_id, category_id) REFERENCES item_category(tenant_id, category_id)
);

CREATE TABLE item_unit_policy (
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    item_id uuid NOT NULL,
    unit_id uuid NOT NULL,
    default_store_id uuid,
    reorder_point numeric(18,3) NOT NULL DEFAULT 0 CHECK (reorder_point >= 0),
    reorder_qty numeric(18,3) NOT NULL DEFAULT 0 CHECK (reorder_qty >= 0),
    PRIMARY KEY (item_id, unit_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, default_store_id) REFERENCES store(tenant_id, store_id)
);

CREATE TABLE payment_term (
    payment_term_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    code text NOT NULL,
    name text NOT NULL,
    due_days integer NOT NULL DEFAULT 0 CHECK (due_days >= 0),
    UNIQUE (tenant_id, payment_term_id),
    UNIQUE (tenant_id, code)
);

CREATE TABLE tax_code (
    tax_code_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    code text NOT NULL,
    description text NOT NULL,
    gst_rate numeric(7,4) NOT NULL CHECK (gst_rate >= 0),
    active boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, tax_code_id),
    UNIQUE (tenant_id, code)
);

CREATE TABLE vendor (
    vendor_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    payment_term_id uuid,
    code text NOT NULL,
    name text NOT NULL,
    gstin varchar(15),
    pan varchar(10),
    msme_flag boolean NOT NULL DEFAULT false,
    status text NOT NULL DEFAULT 'PENDING_APPROVAL' CHECK (status IN ('PENDING_APPROVAL','APPROVED','REJECTED','INACTIVE')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, vendor_id),
    UNIQUE (tenant_id, code),
    FOREIGN KEY (tenant_id, payment_term_id) REFERENCES payment_term(tenant_id, payment_term_id)
);

CREATE TABLE vendor_rating (
    rating_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    vendor_id uuid NOT NULL,
    rating_period date NOT NULL,
    quality_score numeric(5,2) CHECK (quality_score BETWEEN 0 AND 100),
    delivery_score numeric(5,2) CHECK (delivery_score BETWEEN 0 AND 100),
    price_score numeric(5,2) CHECK (price_score BETWEEN 0 AND 100),
    UNIQUE (vendor_id, rating_period),
    FOREIGN KEY (tenant_id, vendor_id) REFERENCES vendor(tenant_id, vendor_id)
);

CREATE TABLE customer (
    customer_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    payment_term_id uuid,
    code text NOT NULL,
    name text NOT NULL,
    gstin varchar(15),
    credit_limit numeric(18,2),
    active boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, customer_id),
    UNIQUE (tenant_id, code),
    FOREIGN KEY (tenant_id, payment_term_id) REFERENCES payment_term(tenant_id, payment_term_id)
);

CREATE TABLE customer_part_xref (
    xref_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    customer_id uuid NOT NULL,
    item_id uuid NOT NULL,
    customer_part_no text NOT NULL,
    UNIQUE (customer_id, customer_part_no),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customer(tenant_id, customer_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id)
);

CREATE TABLE price_list (
    price_list_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    customer_id uuid NOT NULL,
    name text NOT NULL,
    currency_code char(3) NOT NULL DEFAULT 'INR',
    valid_from date NOT NULL,
    valid_to date,
    CHECK (valid_to IS NULL OR valid_to >= valid_from),
    UNIQUE (tenant_id, price_list_id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customer(tenant_id, customer_id)
);

CREATE TABLE price_list_line (
    price_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    price_list_id uuid NOT NULL,
    item_id uuid NOT NULL,
    unit_price numeric(18,2) NOT NULL CHECK (unit_price >= 0),
    UNIQUE (price_list_id, item_id),
    FOREIGN KEY (tenant_id, price_list_id) REFERENCES price_list(tenant_id, price_list_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id)
);

CREATE TABLE customer_rfq (
    customer_rfq_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    customer_id uuid NOT NULL,
    reference_no text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    due_date date,
    status text NOT NULL DEFAULT 'OPEN',
    UNIQUE (tenant_id, customer_rfq_id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customer(tenant_id, customer_id)
);

CREATE TABLE customer_rfq_line (
    customer_rfq_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    customer_rfq_id uuid NOT NULL,
    item_id uuid NOT NULL,
    customer_part_no text,
    requested_qty numeric(18,3) NOT NULL CHECK (requested_qty > 0),
    target_date date,
    UNIQUE (tenant_id, customer_rfq_line_id),
    FOREIGN KEY (tenant_id, customer_rfq_id) REFERENCES customer_rfq(tenant_id, customer_rfq_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id)
);

CREATE TABLE customer_quote (
    customer_quote_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    customer_id uuid NOT NULL,
    customer_rfq_id uuid,
    quote_no text NOT NULL,
    issued_at date NOT NULL,
    valid_until date,
    status text NOT NULL DEFAULT 'DRAFT',
    approval_id uuid,
    UNIQUE (tenant_id, customer_quote_id),
    UNIQUE (tenant_id, quote_no),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customer(tenant_id, customer_id),
    FOREIGN KEY (tenant_id, customer_rfq_id) REFERENCES customer_rfq(tenant_id, customer_rfq_id)
);

CREATE TABLE customer_quote_line (
    customer_quote_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    customer_quote_id uuid NOT NULL,
    item_id uuid NOT NULL,
    quantity numeric(18,3) NOT NULL CHECK (quantity > 0),
    unit_price numeric(18,2) NOT NULL CHECK (unit_price >= 0),
    tax_code_id uuid,
    FOREIGN KEY (tenant_id, customer_quote_id) REFERENCES customer_quote(tenant_id, customer_quote_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id),
    FOREIGN KEY (tenant_id, tax_code_id) REFERENCES tax_code(tenant_id, tax_code_id)
);

-- Approval workflow. document_id is intentionally polymorphic; module service validates it.
CREATE TABLE sla_policy (
    sla_policy_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    name text NOT NULL,
    hours integer NOT NULL CHECK (hours > 0),
    reminder_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    escalation_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (tenant_id, sla_policy_id)
);

CREATE TABLE approval_rule (
    rule_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    sla_policy_id uuid,
    document_type text NOT NULL,
    amount_min numeric(18,2),
    amount_max numeric(18,2),
    active_from timestamptz NOT NULL DEFAULT now(),
    active_to timestamptz,
    priority integer NOT NULL DEFAULT 100,
    version integer NOT NULL DEFAULT 1,
    CHECK (amount_min IS NULL OR amount_min >= 0),
    CHECK (amount_max IS NULL OR amount_max >= amount_min),
    CHECK (active_to IS NULL OR active_to > active_from),
    UNIQUE (tenant_id, rule_id),
    FOREIGN KEY (tenant_id, sla_policy_id) REFERENCES sla_policy(tenant_id, sla_policy_id)
);

CREATE TABLE approval_rule_step (
    rule_step_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    rule_id uuid NOT NULL,
    sequence_no integer NOT NULL CHECK (sequence_no > 0),
    approver_kind text NOT NULL CHECK (approver_kind IN ('ROLE','DEPARTMENT_HEAD','USER')),
    role_id uuid,
    user_id uuid,
    UNIQUE (rule_id, sequence_no),
    FOREIGN KEY (tenant_id, rule_id) REFERENCES approval_rule(tenant_id, rule_id),
    FOREIGN KEY (tenant_id, role_id) REFERENCES role(tenant_id, role_id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES app_user(tenant_id, user_id),
    CHECK ((approver_kind = 'ROLE' AND role_id IS NOT NULL AND user_id IS NULL) OR
           (approver_kind = 'USER' AND user_id IS NOT NULL AND role_id IS NULL) OR
           (approver_kind = 'DEPARTMENT_HEAD' AND role_id IS NULL AND user_id IS NULL))
);

CREATE TABLE approval_instance (
    approval_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    rule_id uuid,
    document_type text NOT NULL,
    document_id uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('PENDING','APPROVED','REJECTED','CANCELLED')),
    current_step integer NOT NULL DEFAULT 1,
    route_snapshot jsonb NOT NULL DEFAULT '[]'::jsonb,
    submitted_by uuid,
    submitted_at timestamptz NOT NULL DEFAULT now(),
    version integer NOT NULL DEFAULT 1,
    UNIQUE (tenant_id, approval_id),
    UNIQUE (tenant_id, document_type, document_id),
    FOREIGN KEY (tenant_id, rule_id) REFERENCES approval_rule(tenant_id, rule_id),
    FOREIGN KEY (tenant_id, submitted_by) REFERENCES app_user(tenant_id, user_id)
);

-- Add this cross-module reference after both customer_quote and approval_instance exist.
ALTER TABLE customer_quote
    ADD CONSTRAINT fk_customer_quote_approval_instance
    FOREIGN KEY (tenant_id, approval_id)
    REFERENCES approval_instance(tenant_id, approval_id);

CREATE TABLE approval_action (
    approval_action_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    approval_id uuid NOT NULL,
    step_no integer NOT NULL CHECK (step_no > 0),
    actor_user_id uuid NOT NULL,
    decision text NOT NULL CHECK (decision IN ('APPROVE','REJECT','OVERRIDE_APPROVE','OVERRIDE_REJECT','SKIP')),
    acting_as_role text,
    note text,
    idempotency_key text NOT NULL,
    decided_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (approval_id, step_no),
    UNIQUE (tenant_id, idempotency_key),
    FOREIGN KEY (tenant_id, approval_id) REFERENCES approval_instance(tenant_id, approval_id),
    FOREIGN KEY (tenant_id, actor_user_id) REFERENCES app_user(tenant_id, user_id)
);

CREATE TABLE approval_delegation (
    delegation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    from_user_id uuid NOT NULL,
    to_user_id uuid NOT NULL,
    role_id uuid,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz NOT NULL,
    active boolean NOT NULL DEFAULT true,
    CHECK (valid_to > valid_from),
    FOREIGN KEY (tenant_id, from_user_id) REFERENCES app_user(tenant_id, user_id),
    FOREIGN KEY (tenant_id, to_user_id) REFERENCES app_user(tenant_id, user_id),
    FOREIGN KEY (tenant_id, role_id) REFERENCES role(tenant_id, role_id)
);

-- Procurement
CREATE TABLE indent (
    indent_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    department_id uuid,
    requester_id uuid NOT NULL,
    indent_no text NOT NULL,
    indent_type text NOT NULL CHECK (indent_type IN ('BOM','CONSUMABLE','CAPITAL')),
    status text NOT NULL DEFAULT 'DRAFT',
    needed_by date,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, indent_id),
    UNIQUE (tenant_id, indent_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, department_id) REFERENCES department(tenant_id, department_id),
    FOREIGN KEY (tenant_id, requester_id) REFERENCES app_user(tenant_id, user_id)
);

CREATE TABLE indent_line (
    indent_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    indent_id uuid NOT NULL,
    line_no integer NOT NULL CHECK (line_no > 0),
    item_id uuid NOT NULL,
    requested_qty numeric(18,3) NOT NULL CHECK (requested_qty > 0),
    purpose text,
    capital_description text,
    capital_value numeric(18,2),
    UNIQUE (indent_id, line_no),
    FOREIGN KEY (tenant_id, indent_id) REFERENCES indent(tenant_id, indent_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id)
);

CREATE TABLE purchase_order (
    po_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    vendor_id uuid NOT NULL,
    po_no text NOT NULL,
    status text NOT NULL DEFAULT 'DRAFT',
    currency_code char(3) NOT NULL DEFAULT 'INR',
    payment_term_id uuid,
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, po_id),
    UNIQUE (tenant_id, po_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, vendor_id) REFERENCES vendor(tenant_id, vendor_id),
    FOREIGN KEY (tenant_id, payment_term_id) REFERENCES payment_term(tenant_id, payment_term_id),
    FOREIGN KEY (tenant_id, created_by) REFERENCES app_user(tenant_id, user_id)
);

CREATE TABLE po_indent_link (
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    po_id uuid NOT NULL,
    indent_id uuid NOT NULL,
    PRIMARY KEY (po_id, indent_id),
    FOREIGN KEY (tenant_id, po_id) REFERENCES purchase_order(tenant_id, po_id),
    FOREIGN KEY (tenant_id, indent_id) REFERENCES indent(tenant_id, indent_id)
);

CREATE TABLE po_line (
    po_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    po_id uuid NOT NULL,
    line_no integer NOT NULL CHECK (line_no > 0),
    item_id uuid NOT NULL,
    ordered_qty numeric(18,3) NOT NULL CHECK (ordered_qty > 0),
    unit_price numeric(18,2) NOT NULL CHECK (unit_price >= 0),
    tax_code_id uuid,
    UNIQUE (tenant_id, po_line_id),
    UNIQUE (po_id, line_no),
    FOREIGN KEY (tenant_id, po_id) REFERENCES purchase_order(tenant_id, po_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id),
    FOREIGN KEY (tenant_id, tax_code_id) REFERENCES tax_code(tenant_id, tax_code_id)
);

CREATE TABLE po_schedule (
    po_schedule_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    po_line_id uuid NOT NULL,
    due_date date NOT NULL,
    scheduled_qty numeric(18,3) NOT NULL CHECK (scheduled_qty > 0),
    received_qty numeric(18,3) NOT NULL DEFAULT 0 CHECK (received_qty >= 0),
    FOREIGN KEY (tenant_id, po_line_id) REFERENCES po_line(tenant_id, po_line_id)
);

-- Inventory ledger and lot traceability
CREATE TABLE lot (
    lot_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    item_id uuid NOT NULL,
    store_id uuid NOT NULL,
    lot_no text NOT NULL,
    supplier_lot_no text,
    received_at timestamptz NOT NULL,
    expiry_at timestamptz,
    status text NOT NULL DEFAULT 'QA_HOLD' CHECK (status IN ('QA_HOLD','AVAILABLE','QUARANTINE','BLOCKED','CONSUMED','EXPIRED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, lot_id),
    UNIQUE (item_id, store_id, lot_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id),
    FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id),
    CHECK (expiry_at IS NULL OR expiry_at >= received_at)
);

CREATE TABLE stock_ledger (
    ledger_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    item_id uuid NOT NULL,
    lot_id uuid NOT NULL,
    store_id uuid NOT NULL,
    qty_delta numeric(18,3) NOT NULL CHECK (qty_delta <> 0),
    movement_type text NOT NULL,
    reference_type text NOT NULL,
    reference_id uuid NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    idempotency_key text NOT NULL,
    created_by uuid,
    UNIQUE (tenant_id, idempotency_key),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id),
    FOREIGN KEY (tenant_id, lot_id) REFERENCES lot(tenant_id, lot_id),
    FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id),
    FOREIGN KEY (tenant_id, created_by) REFERENCES app_user(tenant_id, user_id)
);

-- Transactional balance projection. Update with the ledger in the same transaction,
-- locking this row FOR UPDATE; CHECK is the database backstop against negative on-hand.
CREATE TABLE stock_balance (
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    item_id uuid NOT NULL,
    lot_id uuid NOT NULL,
    store_id uuid NOT NULL,
    quantity_on_hand numeric(18,3) NOT NULL DEFAULT 0 CHECK (quantity_on_hand >= 0),
    quantity_reserved numeric(18,3) NOT NULL DEFAULT 0 CHECK (quantity_reserved >= 0),
    version bigint NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (lot_id, store_id),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id),
    FOREIGN KEY (tenant_id, lot_id) REFERENCES lot(tenant_id, lot_id),
    FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id),
    CHECK (quantity_reserved <= quantity_on_hand)
);

CREATE TABLE cost_layer (
    cost_layer_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    item_id uuid NOT NULL,
    lot_id uuid NOT NULL,
    receipt_ledger_id uuid NOT NULL UNIQUE REFERENCES stock_ledger(ledger_id),
    received_qty numeric(18,3) NOT NULL CHECK (received_qty > 0),
    remaining_qty numeric(18,3) NOT NULL CHECK (remaining_qty >= 0),
    unit_cost numeric(18,6) NOT NULL CHECK (unit_cost >= 0),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id),
    FOREIGN KEY (tenant_id, lot_id) REFERENCES lot(tenant_id, lot_id),
    CHECK (remaining_qty <= received_qty)
);

CREATE TABLE cost_layer_consumption (
    consumption_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    cost_layer_id uuid NOT NULL REFERENCES cost_layer(cost_layer_id),
    issue_ledger_id uuid NOT NULL REFERENCES stock_ledger(ledger_id),
    quantity numeric(18,3) NOT NULL CHECK (quantity > 0),
    extended_cost numeric(18,2) NOT NULL CHECK (extended_cost >= 0),
    UNIQUE (cost_layer_id, issue_ledger_id)
);

CREATE TABLE grn (
    grn_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    po_id uuid NOT NULL,
    grn_no text NOT NULL,
    supplier_dc_no text,
    received_at timestamptz NOT NULL DEFAULT now(),
    status text NOT NULL DEFAULT 'DRAFT',
    posted_by uuid,
    UNIQUE (tenant_id, grn_id),
    UNIQUE (tenant_id, grn_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, po_id) REFERENCES purchase_order(tenant_id, po_id),
    FOREIGN KEY (tenant_id, posted_by) REFERENCES app_user(tenant_id, user_id)
);

CREATE TABLE grn_line (
    grn_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    grn_id uuid NOT NULL,
    po_line_id uuid NOT NULL,
    item_id uuid NOT NULL,
    store_id uuid NOT NULL,
    lot_id uuid NOT NULL,
    received_qty numeric(18,3) NOT NULL CHECK (received_qty > 0),
    accepted_qty numeric(18,3) NOT NULL DEFAULT 0 CHECK (accepted_qty >= 0),
    rejected_qty numeric(18,3) NOT NULL DEFAULT 0 CHECK (rejected_qty >= 0),
    UNIQUE (tenant_id, grn_line_id),
    FOREIGN KEY (tenant_id, grn_id) REFERENCES grn(tenant_id, grn_id),
    FOREIGN KEY (tenant_id, po_line_id) REFERENCES po_line(tenant_id, po_line_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id),
    FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id),
    FOREIGN KEY (tenant_id, lot_id) REFERENCES lot(tenant_id, lot_id),
    CHECK (accepted_qty + rejected_qty <= received_qty)
);

CREATE TABLE stock_count (
    count_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    store_id uuid NOT NULL,
    count_no text NOT NULL,
    period_start date NOT NULL,
    status text NOT NULL DEFAULT 'DRAFT',
    started_at timestamptz NOT NULL DEFAULT now(),
    submitted_by uuid,
    UNIQUE (tenant_id, count_id),
    UNIQUE (tenant_id, count_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id),
    FOREIGN KEY (tenant_id, submitted_by) REFERENCES app_user(tenant_id, user_id)
);

CREATE TABLE stock_count_line (
    count_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    count_id uuid NOT NULL,
    item_id uuid NOT NULL,
    lot_id uuid NOT NULL,
    system_qty numeric(18,3) NOT NULL,
    counted_qty numeric(18,3) NOT NULL CHECK (counted_qty >= 0),
    variance numeric(18,3) GENERATED ALWAYS AS (counted_qty - system_qty) STORED,
    UNIQUE (count_id, lot_id),
    FOREIGN KEY (tenant_id, count_id) REFERENCES stock_count(tenant_id, count_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id),
    FOREIGN KEY (tenant_id, lot_id) REFERENCES lot(tenant_id, lot_id)
);

CREATE TABLE stock_transfer (
    transfer_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    transfer_no text NOT NULL,
    from_store_id uuid NOT NULL,
    to_store_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'DRAFT',
    dispatched_at timestamptz,
    received_at timestamptz,
    UNIQUE (tenant_id, transfer_id),
    UNIQUE (tenant_id, transfer_no),
    FOREIGN KEY (tenant_id, from_store_id) REFERENCES store(tenant_id, store_id),
    FOREIGN KEY (tenant_id, to_store_id) REFERENCES store(tenant_id, store_id),
    CHECK (from_store_id <> to_store_id)
);

CREATE TABLE stock_transfer_line (
    transfer_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    transfer_id uuid NOT NULL,
    item_id uuid NOT NULL,
    lot_id uuid NOT NULL,
    quantity numeric(18,3) NOT NULL CHECK (quantity > 0),
    FOREIGN KEY (tenant_id, transfer_id) REFERENCES stock_transfer(tenant_id, transfer_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id),
    FOREIGN KEY (tenant_id, lot_id) REFERENCES lot(tenant_id, lot_id)
);

-- Supplier returns, bills and 3-way match
CREATE TABLE purchase_return (
    return_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    grn_id uuid NOT NULL,
    vendor_id uuid NOT NULL,
    return_no text NOT NULL,
    debit_note_no text,
    status text NOT NULL DEFAULT 'DRAFT',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, return_id),
    UNIQUE (tenant_id, return_no),
    FOREIGN KEY (tenant_id, grn_id) REFERENCES grn(tenant_id, grn_id),
    FOREIGN KEY (tenant_id, vendor_id) REFERENCES vendor(tenant_id, vendor_id)
);

CREATE TABLE purchase_return_line (
    return_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    return_id uuid NOT NULL,
    grn_line_id uuid NOT NULL,
    lot_id uuid NOT NULL,
    quantity numeric(18,3) NOT NULL CHECK (quantity > 0),
    reason text NOT NULL,
    FOREIGN KEY (tenant_id, return_id) REFERENCES purchase_return(tenant_id, return_id),
    FOREIGN KEY (tenant_id, grn_line_id) REFERENCES grn_line(tenant_id, grn_line_id),
    FOREIGN KEY (tenant_id, lot_id) REFERENCES lot(tenant_id, lot_id)
);

CREATE TABLE supplier_bill (
    bill_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    vendor_id uuid NOT NULL,
    unit_id uuid NOT NULL,
    supplier_invoice_no text NOT NULL,
    invoice_date date NOT NULL,
    taxable_amount numeric(18,2) NOT NULL CHECK (taxable_amount >= 0),
    tax_amount numeric(18,2) NOT NULL DEFAULT 0 CHECK (tax_amount >= 0),
    status text NOT NULL DEFAULT 'DRAFT',
    UNIQUE (tenant_id, bill_id),
    UNIQUE (tenant_id, vendor_id, supplier_invoice_no),
    FOREIGN KEY (tenant_id, vendor_id) REFERENCES vendor(tenant_id, vendor_id),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id)
);

CREATE TABLE supplier_bill_line (
    bill_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    bill_id uuid NOT NULL,
    line_no integer NOT NULL CHECK (line_no > 0),
    description text NOT NULL,
    item_id uuid,
    quantity numeric(18,3) NOT NULL CHECK (quantity >= 0),
    unit_rate numeric(18,2) NOT NULL CHECK (unit_rate >= 0),
    tax_code_id uuid,
    taxable_amount numeric(18,2) NOT NULL CHECK (taxable_amount >= 0),
    tax_amount numeric(18,2) NOT NULL DEFAULT 0 CHECK (tax_amount >= 0),
    UNIQUE (tenant_id, bill_line_id),
    UNIQUE (bill_id, line_no),
    FOREIGN KEY (tenant_id, bill_id) REFERENCES supplier_bill(tenant_id, bill_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id),
    FOREIGN KEY (tenant_id, tax_code_id) REFERENCES tax_code(tenant_id, tax_code_id)
);

CREATE TABLE bill_match (
    bill_match_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    bill_line_id uuid NOT NULL,
    po_line_id uuid NOT NULL,
    grn_line_id uuid NOT NULL,
    billed_qty numeric(18,3) NOT NULL CHECK (billed_qty >= 0),
    billed_rate numeric(18,2) NOT NULL CHECK (billed_rate >= 0),
    qty_variance numeric(18,3) NOT NULL DEFAULT 0,
    rate_variance numeric(18,2) NOT NULL DEFAULT 0,
    exception_status text NOT NULL DEFAULT 'NONE',
    FOREIGN KEY (tenant_id, bill_line_id) REFERENCES supplier_bill_line(tenant_id, bill_line_id),
    FOREIGN KEY (tenant_id, po_line_id) REFERENCES po_line(tenant_id, po_line_id),
    FOREIGN KEY (tenant_id, grn_line_id) REFERENCES grn_line(tenant_id, grn_line_id)
);

CREATE TABLE cash_purchase (
    cash_purchase_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    store_id uuid,
    voucher_no text NOT NULL,
    description text NOT NULL,
    amount numeric(18,2) NOT NULL CHECK (amount > 0),
    purchased_at timestamptz NOT NULL DEFAULT now(),
    created_by uuid NOT NULL,
    status text NOT NULL DEFAULT 'DRAFT',
    UNIQUE (tenant_id, voucher_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, store_id) REFERENCES store(tenant_id, store_id),
    FOREIGN KEY (tenant_id, created_by) REFERENCES app_user(tenant_id, user_id)
);

CREATE TABLE rfq (
    rfq_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    rfq_no text NOT NULL,
    status text NOT NULL DEFAULT 'DRAFT',
    response_due_at timestamptz,
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, rfq_id),
    UNIQUE (tenant_id, rfq_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, created_by) REFERENCES app_user(tenant_id, user_id)
);

CREATE TABLE rfq_line (
    rfq_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    rfq_id uuid NOT NULL,
    item_id uuid NOT NULL,
    line_no integer NOT NULL CHECK (line_no > 0),
    quantity numeric(18,3) NOT NULL CHECK (quantity > 0),
    required_by date,
    UNIQUE (rfq_id, line_no),
    UNIQUE (tenant_id, rfq_line_id),
    FOREIGN KEY (tenant_id, rfq_id) REFERENCES rfq(tenant_id, rfq_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id)
);

CREATE TABLE rfq_vendor_invitation (
    invitation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    rfq_id uuid NOT NULL,
    vendor_id uuid NOT NULL,
    invited_at timestamptz NOT NULL DEFAULT now(),
    response_status text NOT NULL DEFAULT 'INVITED',
    UNIQUE (rfq_id, vendor_id),
    FOREIGN KEY (tenant_id, rfq_id) REFERENCES rfq(tenant_id, rfq_id),
    FOREIGN KEY (tenant_id, vendor_id) REFERENCES vendor(tenant_id, vendor_id)
);

CREATE TABLE vendor_quote (
    vendor_quote_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    invitation_id uuid NOT NULL REFERENCES rfq_vendor_invitation(invitation_id),
    quote_reference text,
    submitted_at timestamptz NOT NULL DEFAULT now(),
    valid_until date,
    status text NOT NULL DEFAULT 'RECEIVED'
);

CREATE TABLE vendor_quote_line (
    vendor_quote_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    vendor_quote_id uuid NOT NULL REFERENCES vendor_quote(vendor_quote_id),
    rfq_line_id uuid NOT NULL,
    unit_price numeric(18,2) NOT NULL CHECK (unit_price >= 0),
    quoted_qty numeric(18,3) NOT NULL CHECK (quoted_qty >= 0),
    lead_days integer CHECK (lead_days IS NULL OR lead_days >= 0),
    UNIQUE (vendor_quote_id, rfq_line_id),
    FOREIGN KEY (tenant_id, rfq_line_id) REFERENCES rfq_line(tenant_id, rfq_line_id)
);

-- Quality inspection, NCR/8D and calibration. Typed links keep core QA subjects referentially valid.
CREATE TABLE qa_inspection (
    inspection_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    inspection_no text NOT NULL,
    inspected_by uuid NOT NULL,
    result text NOT NULL CHECK (result IN ('ACCEPT','PARTIAL','REJECT','HOLD')),
    inspected_at timestamptz NOT NULL DEFAULT now(),
    notes text,
    UNIQUE (tenant_id, inspection_id),
    UNIQUE (tenant_id, inspection_no),
    FOREIGN KEY (tenant_id, inspected_by) REFERENCES app_user(tenant_id, user_id)
);

CREATE TABLE qa_inspection_grn_line (
    inspection_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    grn_line_id uuid NOT NULL,
    inspected_qty numeric(18,3) NOT NULL CHECK (inspected_qty > 0),
    accepted_qty numeric(18,3) NOT NULL DEFAULT 0 CHECK (accepted_qty >= 0),
    UNIQUE (grn_line_id, inspection_id),
    FOREIGN KEY (tenant_id, inspection_id) REFERENCES qa_inspection(tenant_id, inspection_id),
    FOREIGN KEY (tenant_id, grn_line_id) REFERENCES grn_line(tenant_id, grn_line_id),
    CHECK (accepted_qty <= inspected_qty)
);

CREATE TABLE qa_measurement (
    measurement_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    inspection_id uuid NOT NULL,
    parameter_code text NOT NULL,
    measured_value numeric(18,6),
    unit text,
    result text CHECK (result IN ('PASS','FAIL','INFO')),
    FOREIGN KEY (tenant_id, inspection_id) REFERENCES qa_inspection(tenant_id, inspection_id)
);

CREATE TABLE ncr (
    ncr_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    ncr_no text NOT NULL,
    source_type text NOT NULL CHECK (source_type IN ('GRN_LINE','COMPOUND_BATCH','PROD_LOT','SUPPLIER','CUSTOMER_COMPLAINT')),
    source_id uuid NOT NULL,
    owner_user_id uuid,
    title text NOT NULL,
    status text NOT NULL DEFAULT 'OPEN',
    opened_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, ncr_id),
    UNIQUE (tenant_id, ncr_no),
    FOREIGN KEY (tenant_id, owner_user_id) REFERENCES app_user(tenant_id, user_id)
);

CREATE TABLE ncr_step (
    ncr_step_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    ncr_id uuid NOT NULL,
    step_code text NOT NULL,
    owner_user_id uuid,
    due_at timestamptz,
    completed_at timestamptz,
    action_text text,
    evidence_file_key text,
    FOREIGN KEY (tenant_id, ncr_id) REFERENCES ncr(tenant_id, ncr_id),
    FOREIGN KEY (tenant_id, owner_user_id) REFERENCES app_user(tenant_id, user_id)
);

CREATE TABLE instrument (
    instrument_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    UNIQUE (unit_id, code),
    UNIQUE (tenant_id, instrument_id),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id)
);

CREATE TABLE calibration (
    calibration_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    instrument_id uuid NOT NULL,
    calibrated_at date NOT NULL,
    due_at date NOT NULL,
    result text NOT NULL CHECK (result IN ('PASS','FAIL','CONDITIONAL')),
    certificate_ref text,
    FOREIGN KEY (tenant_id, instrument_id) REFERENCES instrument(tenant_id, instrument_id)
);

-- Production masters, compound lifecycle, routes and actual consumption
CREATE TABLE compound (
    compound_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    code text NOT NULL,
    name text NOT NULL,
    lane text NOT NULL CHECK (lane IN ('RUBBER','EXTRUSION')),
    maturation_min_hours numeric(8,2) NOT NULL CHECK (maturation_min_hours >= 0),
    maximum_age_hours numeric(8,2) NOT NULL CHECK (maximum_age_hours > maturation_min_hours),
    hardness_min numeric(8,2),
    hardness_max numeric(8,2),
    active boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, compound_id),
    UNIQUE (tenant_id, code),
    CHECK (hardness_min IS NULL OR hardness_max IS NULL OR hardness_max >= hardness_min)
);

CREATE TABLE compound_recipe_line (
    recipe_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    compound_id uuid NOT NULL,
    item_id uuid NOT NULL,
    line_no integer NOT NULL CHECK (line_no > 0),
    phr numeric(18,6) NOT NULL CHECK (phr > 0),
    UNIQUE (tenant_id, recipe_line_id),
    UNIQUE (compound_id, line_no),
    UNIQUE (compound_id, item_id),
    FOREIGN KEY (tenant_id, compound_id) REFERENCES compound(tenant_id, compound_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id)
);

CREATE TABLE compound_batch (
    batch_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    compound_id uuid NOT NULL,
    batch_no text NOT NULL,
    weighed_at timestamptz,
    mixed_at timestamptz,
    total_kg numeric(18,3) NOT NULL CHECK (total_kg > 0),
    remaining_kg numeric(18,3) NOT NULL CHECK (remaining_kg >= 0),
    qa_status text NOT NULL DEFAULT 'PENDING' CHECK (qa_status IN ('PENDING','PASSED','FAILED')),
    hardness numeric(8,2),
    created_by uuid,
    UNIQUE (tenant_id, batch_id),
    UNIQUE (tenant_id, batch_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, compound_id) REFERENCES compound(tenant_id, compound_id),
    FOREIGN KEY (tenant_id, created_by) REFERENCES app_user(tenant_id, user_id),
    CHECK (remaining_kg <= total_kg)
);

CREATE TABLE batch_weighing_line (
    weighing_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    batch_id uuid NOT NULL,
    recipe_line_id uuid NOT NULL,
    source_lot_id uuid NOT NULL,
    target_qty numeric(18,3) NOT NULL CHECK (target_qty > 0),
    actual_qty numeric(18,3) NOT NULL CHECK (actual_qty >= 0),
    FOREIGN KEY (tenant_id, batch_id) REFERENCES compound_batch(tenant_id, batch_id),
    FOREIGN KEY (tenant_id, recipe_line_id) REFERENCES compound_recipe_line(tenant_id, recipe_line_id),
    FOREIGN KEY (tenant_id, source_lot_id) REFERENCES lot(tenant_id, lot_id)
);

CREATE TABLE route (
    route_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    code text NOT NULL,
    name text NOT NULL,
    version integer NOT NULL DEFAULT 1,
    active boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, route_id),
    UNIQUE (tenant_id, code, version)
);

CREATE TABLE route_stage (
    route_stage_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    route_id uuid NOT NULL,
    sequence_no integer NOT NULL CHECK (sequence_no > 0),
    stage_code text NOT NULL,
    stage_name text NOT NULL,
    is_qa_gate boolean NOT NULL DEFAULT false,
    is_end boolean NOT NULL DEFAULT false,
    UNIQUE (route_id, sequence_no),
    UNIQUE (tenant_id, route_stage_id),
    FOREIGN KEY (tenant_id, route_id) REFERENCES route(tenant_id, route_id)
);

CREATE TABLE bom (
    bom_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    item_id uuid NOT NULL,
    version integer NOT NULL DEFAULT 1,
    valid_from date NOT NULL,
    valid_to date,
    status text NOT NULL DEFAULT 'DRAFT',
    UNIQUE (tenant_id, bom_id),
    UNIQUE (tenant_id, item_id, version),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE TABLE bom_line (
    bom_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    bom_id uuid NOT NULL,
    component_item_id uuid NOT NULL,
    line_no integer NOT NULL CHECK (line_no > 0),
    quantity_per numeric(18,6) NOT NULL CHECK (quantity_per > 0),
    scrap_factor numeric(8,6) NOT NULL DEFAULT 0 CHECK (scrap_factor >= 0),
    UNIQUE (bom_id, line_no),
    FOREIGN KEY (tenant_id, bom_id) REFERENCES bom(tenant_id, bom_id),
    FOREIGN KEY (tenant_id, component_item_id) REFERENCES item(tenant_id, item_id)
);

CREATE TABLE work_order (
    work_order_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    item_id uuid NOT NULL,
    bom_id uuid NOT NULL,
    route_id uuid NOT NULL,
    source_so_line_id uuid,
    work_order_no text NOT NULL,
    planned_qty numeric(18,3) NOT NULL CHECK (planned_qty > 0),
    status text NOT NULL DEFAULT 'PLANNED',
    planned_start date,
    planned_finish date,
    UNIQUE (tenant_id, work_order_id),
    UNIQUE (tenant_id, work_order_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id),
    FOREIGN KEY (tenant_id, bom_id) REFERENCES bom(tenant_id, bom_id),
    FOREIGN KEY (tenant_id, route_id) REFERENCES route(tenant_id, route_id)
);

CREATE TABLE prod_lot (
    prod_lot_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    work_order_id uuid NOT NULL,
    item_id uuid NOT NULL,
    route_id uuid NOT NULL,
    current_stage_id uuid NOT NULL,
    lot_no text NOT NULL,
    quantity numeric(18,3) NOT NULL CHECK (quantity >= 0),
    status text NOT NULL DEFAULT 'IN_PROCESS',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, prod_lot_id),
    UNIQUE (tenant_id, lot_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, work_order_id) REFERENCES work_order(tenant_id, work_order_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id),
    FOREIGN KEY (tenant_id, route_id) REFERENCES route(tenant_id, route_id),
    FOREIGN KEY (tenant_id, current_stage_id) REFERENCES route_stage(tenant_id, route_stage_id)
);

CREATE TABLE prod_lot_move (
    move_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    prod_lot_id uuid NOT NULL,
    from_stage_id uuid,
    to_stage_id uuid NOT NULL,
    actor_user_id uuid NOT NULL,
    quantity_in numeric(18,3) NOT NULL CHECK (quantity_in >= 0),
    quantity_out numeric(18,3) NOT NULL CHECK (quantity_out >= 0),
    rejects numeric(18,3) NOT NULL DEFAULT 0 CHECK (rejects >= 0),
    moved_at timestamptz NOT NULL DEFAULT now(),
    notes text,
    FOREIGN KEY (tenant_id, prod_lot_id) REFERENCES prod_lot(tenant_id, prod_lot_id),
    FOREIGN KEY (tenant_id, from_stage_id) REFERENCES route_stage(tenant_id, route_stage_id),
    FOREIGN KEY (tenant_id, to_stage_id) REFERENCES route_stage(tenant_id, route_stage_id),
    FOREIGN KEY (tenant_id, actor_user_id) REFERENCES app_user(tenant_id, user_id)
);

CREATE TABLE lot_consumption (
    consumption_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    prod_lot_id uuid NOT NULL,
    source_lot_id uuid NOT NULL,
    source_batch_id uuid,
    consumption_type text NOT NULL CHECK (consumption_type IN ('RAW_MATERIAL','COMPOUND_BATCH','METAL_PROD_LOT','OTHER')),
    quantity numeric(18,3) NOT NULL CHECK (quantity > 0),
    consumed_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, prod_lot_id) REFERENCES prod_lot(tenant_id, prod_lot_id),
    FOREIGN KEY (tenant_id, source_lot_id) REFERENCES lot(tenant_id, lot_id),
    FOREIGN KEY (tenant_id, source_batch_id) REFERENCES compound_batch(tenant_id, batch_id)
);

CREATE TABLE machine (
    machine_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    machine_type text,
    active boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, machine_id),
    UNIQUE (unit_id, code),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id)
);

CREATE TABLE mould (
    mould_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    part_item_id uuid NOT NULL,
    code text NOT NULL,
    shots_limit bigint,
    active boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, mould_id),
    UNIQUE (unit_id, code),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, part_item_id) REFERENCES item(tenant_id, item_id)
);

CREATE TABLE shift_production (
    shift_entry_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    work_order_id uuid NOT NULL,
    machine_id uuid NOT NULL,
    operator_id uuid NOT NULL,
    work_date date NOT NULL,
    shift_code text NOT NULL CHECK (shift_code IN ('A','B','C')),
    good_qty numeric(18,3) NOT NULL DEFAULT 0 CHECK (good_qty >= 0),
    reject_qty numeric(18,3) NOT NULL DEFAULT 0 CHECK (reject_qty >= 0),
    downtime_minutes integer NOT NULL DEFAULT 0 CHECK (downtime_minutes >= 0),
    UNIQUE (work_order_id, machine_id, work_date, shift_code),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, work_order_id) REFERENCES work_order(tenant_id, work_order_id),
    FOREIGN KEY (tenant_id, machine_id) REFERENCES machine(tenant_id, machine_id),
    FOREIGN KEY (tenant_id, operator_id) REFERENCES app_user(tenant_id, user_id)
);

CREATE TABLE packing (
    packing_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    prod_lot_id uuid NOT NULL,
    fg_store_id uuid NOT NULL,
    packed_by uuid NOT NULL,
    packed_qty numeric(18,3) NOT NULL CHECK (packed_qty > 0),
    packed_at timestamptz NOT NULL DEFAULT now(),
    label_code text NOT NULL,
    FOREIGN KEY (tenant_id, prod_lot_id) REFERENCES prod_lot(tenant_id, prod_lot_id),
    FOREIGN KEY (tenant_id, fg_store_id) REFERENCES store(tenant_id, store_id),
    FOREIGN KEY (tenant_id, packed_by) REFERENCES app_user(tenant_id, user_id)
);

-- Sales: customer orders, schedule imports, dispatch, invoices and challans
CREATE TABLE schedule_import (
    import_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    uploaded_by uuid NOT NULL,
    source_file_key text,
    source_type text NOT NULL CHECK (source_type IN ('XLSX','CSV','EMAIL','MANUAL')),
    status text NOT NULL DEFAULT 'UPLOADED',
    parsed_rows jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, import_id),
    FOREIGN KEY (tenant_id, uploaded_by) REFERENCES app_user(tenant_id, user_id)
);

CREATE TABLE sales_order (
    so_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    customer_id uuid NOT NULL,
    so_no text NOT NULL,
    customer_po_no text,
    order_date date NOT NULL,
    status text NOT NULL DEFAULT 'OPEN',
    currency_code char(3) NOT NULL DEFAULT 'INR',
    UNIQUE (tenant_id, so_id),
    UNIQUE (tenant_id, so_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customer(tenant_id, customer_id)
);

CREATE TABLE so_line (
    so_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    so_id uuid NOT NULL,
    line_no integer NOT NULL CHECK (line_no > 0),
    item_id uuid NOT NULL,
    ordered_qty numeric(18,3) NOT NULL CHECK (ordered_qty > 0),
    unit_price numeric(18,2) NOT NULL CHECK (unit_price >= 0),
    tax_code_id uuid,
    UNIQUE (tenant_id, so_line_id),
    UNIQUE (so_id, line_no),
    FOREIGN KEY (tenant_id, so_id) REFERENCES sales_order(tenant_id, so_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id),
    FOREIGN KEY (tenant_id, tax_code_id) REFERENCES tax_code(tenant_id, tax_code_id)
);

CREATE TABLE so_schedule (
    so_schedule_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    so_line_id uuid NOT NULL,
    import_id uuid,
    due_date date NOT NULL,
    scheduled_qty numeric(18,3) NOT NULL CHECK (scheduled_qty > 0),
    fulfilled_qty numeric(18,3) NOT NULL DEFAULT 0 CHECK (fulfilled_qty >= 0),
    FOREIGN KEY (tenant_id, so_line_id) REFERENCES so_line(tenant_id, so_line_id),
    FOREIGN KEY (tenant_id, import_id) REFERENCES schedule_import(tenant_id, import_id),
    CHECK (fulfilled_qty <= scheduled_qty)
);

CREATE TABLE dispatch (
    dispatch_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    so_id uuid NOT NULL,
    dispatch_no text NOT NULL,
    status text NOT NULL DEFAULT 'DRAFT',
    dispatched_at timestamptz,
    created_by uuid NOT NULL,
    UNIQUE (tenant_id, dispatch_id),
    UNIQUE (tenant_id, dispatch_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, so_id) REFERENCES sales_order(tenant_id, so_id),
    FOREIGN KEY (tenant_id, created_by) REFERENCES app_user(tenant_id, user_id)
);

CREATE TABLE dispatch_line (
    dispatch_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    dispatch_id uuid NOT NULL,
    so_line_id uuid NOT NULL,
    lot_id uuid NOT NULL,
    quantity numeric(18,3) NOT NULL CHECK (quantity > 0),
    FOREIGN KEY (tenant_id, dispatch_id) REFERENCES dispatch(tenant_id, dispatch_id),
    FOREIGN KEY (tenant_id, so_line_id) REFERENCES so_line(tenant_id, so_line_id),
    FOREIGN KEY (tenant_id, lot_id) REFERENCES lot(tenant_id, lot_id)
);

CREATE TABLE customer_invoice (
    invoice_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    customer_id uuid NOT NULL,
    dispatch_id uuid NOT NULL,
    invoice_no text NOT NULL,
    issue_date date NOT NULL,
    taxable_amount numeric(18,2) NOT NULL CHECK (taxable_amount >= 0),
    tax_amount numeric(18,2) NOT NULL DEFAULT 0 CHECK (tax_amount >= 0),
    irn text,
    irn_ack_no text,
    ewb_no text,
    status text NOT NULL DEFAULT 'DRAFT',
    UNIQUE (tenant_id, invoice_id),
    UNIQUE (tenant_id, invoice_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customer(tenant_id, customer_id),
    FOREIGN KEY (tenant_id, dispatch_id) REFERENCES dispatch(tenant_id, dispatch_id)
);

CREATE TABLE invoice_line (
    invoice_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    invoice_id uuid NOT NULL,
    so_line_id uuid NOT NULL,
    item_id uuid NOT NULL,
    quantity numeric(18,3) NOT NULL CHECK (quantity > 0),
    unit_price numeric(18,2) NOT NULL CHECK (unit_price >= 0),
    taxable_amount numeric(18,2) NOT NULL CHECK (taxable_amount >= 0),
    tax_amount numeric(18,2) NOT NULL DEFAULT 0 CHECK (tax_amount >= 0),
    FOREIGN KEY (tenant_id, invoice_id) REFERENCES customer_invoice(tenant_id, invoice_id),
    FOREIGN KEY (tenant_id, so_line_id) REFERENCES so_line(tenant_id, so_line_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id)
);

CREATE TABLE challan (
    challan_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    customer_id uuid,
    challan_no text NOT NULL,
    challan_type text NOT NULL CHECK (challan_type IN ('RETURNABLE','NON_RETURNABLE','FOC','JOB_WORK')),
    issued_at timestamptz NOT NULL DEFAULT now(),
    due_back date,
    status text NOT NULL DEFAULT 'OUT',
    UNIQUE (tenant_id, challan_id),
    UNIQUE (tenant_id, challan_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customer(tenant_id, customer_id)
);

CREATE TABLE challan_line (
    challan_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    challan_id uuid NOT NULL,
    item_id uuid NOT NULL,
    lot_id uuid,
    quantity numeric(18,3) NOT NULL CHECK (quantity > 0),
    returned_qty numeric(18,3) NOT NULL DEFAULT 0 CHECK (returned_qty >= 0),
    FOREIGN KEY (tenant_id, challan_id) REFERENCES challan(tenant_id, challan_id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id),
    FOREIGN KEY (tenant_id, lot_id) REFERENCES lot(tenant_id, lot_id),
    CHECK (returned_qty <= quantity)
);

CREATE TABLE scrap_sale (
    scrap_sale_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    customer_id uuid,
    sale_no text NOT NULL,
    description text NOT NULL,
    quantity numeric(18,3) NOT NULL CHECK (quantity > 0),
    amount numeric(18,2) NOT NULL CHECK (amount >= 0),
    status text NOT NULL DEFAULT 'PENDING_APPROVAL',
    UNIQUE (tenant_id, scrap_sale_id),
    UNIQUE (tenant_id, sale_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customer(tenant_id, customer_id)
);

CREATE TABLE inter_unit_invoice (
    inter_unit_invoice_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    from_unit_id uuid NOT NULL,
    to_unit_id uuid NOT NULL,
    transfer_id uuid NOT NULL,
    invoice_no text NOT NULL,
    irn text,
    ewb_no text,
    status text NOT NULL DEFAULT 'DRAFT',
    UNIQUE (tenant_id, inter_unit_invoice_id),
    UNIQUE (tenant_id, invoice_no),
    FOREIGN KEY (tenant_id, from_unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, to_unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, transfer_id) REFERENCES stock_transfer(tenant_id, transfer_id),
    CHECK (from_unit_id <> to_unit_id)
);

-- Finance, GST and payments
CREATE TABLE fiscal_period (
    period_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    year_month date NOT NULL,
    status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED')),
    production_grace_days smallint NOT NULL DEFAULT 1 CHECK (production_grace_days BETWEEN 0 AND 3),
    closed_at timestamptz,
    UNIQUE (tenant_id, period_id),
    UNIQUE (unit_id, year_month),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    CHECK (EXTRACT(DAY FROM year_month) = 1)
);

CREATE TABLE account (
    account_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    account_type text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, account_id),
    UNIQUE (unit_id, code),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id)
);

CREATE TABLE journal (
    journal_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    period_id uuid NOT NULL,
    journal_no text NOT NULL,
    source_type text NOT NULL,
    source_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'DRAFT',
    posted_at timestamptz,
    UNIQUE (tenant_id, journal_id),
    UNIQUE (tenant_id, journal_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, period_id) REFERENCES fiscal_period(tenant_id, period_id)
);

CREATE TABLE journal_line (
    journal_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    journal_id uuid NOT NULL,
    account_id uuid NOT NULL,
    line_no integer NOT NULL CHECK (line_no > 0),
    description text,
    debit numeric(18,2) NOT NULL DEFAULT 0 CHECK (debit >= 0),
    credit numeric(18,2) NOT NULL DEFAULT 0 CHECK (credit >= 0),
    party_type text,
    party_id uuid,
    UNIQUE (journal_id, line_no),
    FOREIGN KEY (tenant_id, journal_id) REFERENCES journal(tenant_id, journal_id),
    FOREIGN KEY (tenant_id, account_id) REFERENCES account(tenant_id, account_id),
    CHECK ((debit = 0) <> (credit = 0))
);

CREATE TABLE payment (
    payment_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    payment_no text NOT NULL,
    vendor_id uuid,
    customer_id uuid,
    amount numeric(18,2) NOT NULL CHECK (amount > 0),
    currency_code char(3) NOT NULL DEFAULT 'INR',
    payment_date date,
    status text NOT NULL DEFAULT 'PROPOSED',
    UNIQUE (tenant_id, payment_id),
    UNIQUE (tenant_id, payment_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, vendor_id) REFERENCES vendor(tenant_id, vendor_id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customer(tenant_id, customer_id),
    CHECK ((vendor_id IS NULL) <> (customer_id IS NULL))
);

CREATE TABLE payment_allocation (
    allocation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    payment_id uuid NOT NULL,
    bill_id uuid,
    invoice_id uuid,
    allocated_amount numeric(18,2) NOT NULL CHECK (allocated_amount > 0),
    FOREIGN KEY (tenant_id, payment_id) REFERENCES payment(tenant_id, payment_id),
    FOREIGN KEY (tenant_id, bill_id) REFERENCES supplier_bill(tenant_id, bill_id),
    FOREIGN KEY (tenant_id, invoice_id) REFERENCES customer_invoice(tenant_id, invoice_id),
    CHECK ((bill_id IS NULL) <> (invoice_id IS NULL))
);

CREATE TABLE gst_return_data (
    return_data_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    period_id uuid NOT NULL,
    return_type text NOT NULL CHECK (return_type IN ('GSTR1','GSTR3B','GSTR2B','TDS')),
    status text NOT NULL DEFAULT 'PREPARED',
    source_reference text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    filed_at timestamptz,
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, period_id) REFERENCES fiscal_period(tenant_id, period_id)
);

CREATE TABLE tds_entry (
    tds_entry_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    period_id uuid NOT NULL,
    vendor_id uuid,
    customer_id uuid,
    section_code text NOT NULL,
    taxable_base numeric(18,2) NOT NULL CHECK (taxable_base >= 0),
    deducted_amount numeric(18,2) NOT NULL CHECK (deducted_amount >= 0),
    status text NOT NULL DEFAULT 'OPEN',
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, period_id) REFERENCES fiscal_period(tenant_id, period_id),
    FOREIGN KEY (tenant_id, vendor_id) REFERENCES vendor(tenant_id, vendor_id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customer(tenant_id, customer_id),
    CHECK ((vendor_id IS NULL) <> (customer_id IS NULL))
);

-- Maintenance, assets and depreciation
CREATE TABLE pm_plan (
    pm_plan_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    machine_id uuid,
    mould_id uuid,
    interval_days integer,
    interval_shots bigint,
    active boolean NOT NULL DEFAULT true,
    CHECK ((machine_id IS NULL) <> (mould_id IS NULL)),
    CHECK (interval_days IS NOT NULL OR interval_shots IS NOT NULL),
    FOREIGN KEY (tenant_id, machine_id) REFERENCES machine(tenant_id, machine_id),
    FOREIGN KEY (tenant_id, mould_id) REFERENCES mould(tenant_id, mould_id)
);

CREATE TABLE pm_task (
    pm_task_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    pm_plan_id uuid NOT NULL REFERENCES pm_plan(pm_plan_id),
    due_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'PLANNED',
    completed_at timestamptz,
    owner_user_id uuid,
    notes text,
    FOREIGN KEY (tenant_id, owner_user_id) REFERENCES app_user(tenant_id, user_id)
);

CREATE TABLE breakdown (
    breakdown_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    machine_id uuid NOT NULL,
    reported_by uuid NOT NULL,
    breakdown_no text NOT NULL,
    cause text,
    corrective_action text,
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,
    status text NOT NULL DEFAULT 'OPEN',
    UNIQUE (tenant_id, breakdown_no),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, machine_id) REFERENCES machine(tenant_id, machine_id),
    FOREIGN KEY (tenant_id, reported_by) REFERENCES app_user(tenant_id, user_id),
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE TABLE spare_issue (
    spare_issue_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    breakdown_id uuid NOT NULL REFERENCES breakdown(breakdown_id),
    item_id uuid NOT NULL,
    lot_id uuid NOT NULL,
    stock_ledger_id uuid NOT NULL UNIQUE REFERENCES stock_ledger(ledger_id),
    quantity numeric(18,3) NOT NULL CHECK (quantity > 0),
    FOREIGN KEY (tenant_id, item_id) REFERENCES item(tenant_id, item_id),
    FOREIGN KEY (tenant_id, lot_id) REFERENCES lot(tenant_id, lot_id)
);

CREATE TABLE fixed_asset (
    asset_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    asset_code text NOT NULL,
    description text NOT NULL,
    acquisition_date date NOT NULL,
    acquisition_cost numeric(18,2) NOT NULL CHECK (acquisition_cost >= 0),
    useful_life_months integer NOT NULL CHECK (useful_life_months > 0),
    status text NOT NULL DEFAULT 'ACTIVE',
    UNIQUE (tenant_id, asset_id),
    UNIQUE (unit_id, asset_code),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id)
);

CREATE TABLE depreciation_run (
    depreciation_run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    period_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'DRAFT',
    run_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, depreciation_run_id),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, period_id) REFERENCES fiscal_period(tenant_id, period_id)
);

CREATE TABLE depreciation_line (
    depreciation_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    depreciation_run_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    depreciation_amount numeric(18,2) NOT NULL CHECK (depreciation_amount >= 0),
    accumulated_amount numeric(18,2) NOT NULL CHECK (accumulated_amount >= 0),
    UNIQUE (depreciation_run_id, asset_id),
    FOREIGN KEY (tenant_id, depreciation_run_id) REFERENCES depreciation_run(tenant_id, depreciation_run_id),
    FOREIGN KEY (tenant_id, asset_id) REFERENCES fixed_asset(tenant_id, asset_id)
);

-- HR and continuous improvement
CREATE TABLE employee (
    employee_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    department_id uuid NOT NULL,
    manager_id uuid,
    user_id uuid,
    employee_no text NOT NULL,
    full_name text NOT NULL,
    job_title text,
    employment_status text NOT NULL DEFAULT 'ACTIVE',
    date_joined date,
    UNIQUE (tenant_id, employee_id),
    UNIQUE (tenant_id, employee_no),
    UNIQUE (tenant_id, user_id),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, department_id) REFERENCES department(tenant_id, department_id),
    FOREIGN KEY (tenant_id, manager_id) REFERENCES employee(tenant_id, employee_id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES app_user(tenant_id, user_id)
);

CREATE TABLE leave_type (
    leave_type_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    code text NOT NULL,
    name text NOT NULL,
    annual_default_days numeric(8,2) NOT NULL DEFAULT 0 CHECK (annual_default_days >= 0),
    UNIQUE (tenant_id, leave_type_id),
    UNIQUE (tenant_id, code)
);

CREATE TABLE leave_balance (
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    employee_id uuid NOT NULL,
    leave_type_id uuid NOT NULL,
    fiscal_year integer NOT NULL,
    opening_days numeric(8,2) NOT NULL DEFAULT 0,
    accrued_days numeric(8,2) NOT NULL DEFAULT 0,
    used_days numeric(8,2) NOT NULL DEFAULT 0,
    adjusted_days numeric(8,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (employee_id, leave_type_id, fiscal_year),
    FOREIGN KEY (tenant_id, employee_id) REFERENCES employee(tenant_id, employee_id),
    FOREIGN KEY (tenant_id, leave_type_id) REFERENCES leave_type(tenant_id, leave_type_id)
);

CREATE TABLE leave_request (
    leave_request_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    employee_id uuid NOT NULL,
    leave_type_id uuid NOT NULL,
    start_date date NOT NULL,
    end_date date NOT NULL,
    requested_days numeric(8,2) NOT NULL CHECK (requested_days > 0),
    reason text,
    status text NOT NULL DEFAULT 'SUBMITTED',
    FOREIGN KEY (tenant_id, employee_id) REFERENCES employee(tenant_id, employee_id),
    FOREIGN KEY (tenant_id, leave_type_id) REFERENCES leave_type(tenant_id, leave_type_id),
    CHECK (end_date >= start_date)
);

CREATE TABLE attendance (
    attendance_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    employee_id uuid NOT NULL,
    work_date date NOT NULL,
    shift_code text NOT NULL,
    check_in timestamptz,
    check_out timestamptz,
    source text NOT NULL DEFAULT 'MANUAL',
    UNIQUE (employee_id, work_date, shift_code),
    FOREIGN KEY (tenant_id, employee_id) REFERENCES employee(tenant_id, employee_id),
    CHECK (check_out IS NULL OR check_in IS NULL OR check_out >= check_in)
);

CREATE TABLE appraisal (
    appraisal_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    employee_id uuid NOT NULL,
    fiscal_year integer NOT NULL,
    self_score numeric(5,2),
    manager_score numeric(5,2),
    hr_score numeric(5,2),
    status text NOT NULL DEFAULT 'SELF_REVIEW',
    UNIQUE (employee_id, fiscal_year),
    FOREIGN KEY (tenant_id, employee_id) REFERENCES employee(tenant_id, employee_id)
);

CREATE TABLE payroll_run (
    payroll_run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    status text NOT NULL DEFAULT 'DRAFT',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (period_end >= period_start),
    UNIQUE (tenant_id, payroll_run_id),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id)
);

CREATE TABLE payroll_line (
    payroll_line_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    payroll_run_id uuid NOT NULL,
    employee_id uuid NOT NULL,
    gross_amount numeric(18,2) NOT NULL CHECK (gross_amount >= 0),
    deduction_amount numeric(18,2) NOT NULL DEFAULT 0 CHECK (deduction_amount >= 0),
    net_amount numeric(18,2) NOT NULL CHECK (net_amount >= 0),
    UNIQUE (payroll_run_id, employee_id),
    FOREIGN KEY (tenant_id, payroll_run_id) REFERENCES payroll_run(tenant_id, payroll_run_id),
    FOREIGN KEY (tenant_id, employee_id) REFERENCES employee(tenant_id, employee_id)
);

CREATE TABLE kaizen (
    kaizen_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    unit_id uuid NOT NULL,
    department_id uuid NOT NULL,
    submitter_id uuid NOT NULL,
    title text NOT NULL,
    problem_description text NOT NULL,
    proposed_change text NOT NULL,
    baseline_value numeric(18,3),
    achieved_value numeric(18,3),
    estimated_saving numeric(18,2),
    score numeric(6,2),
    status text NOT NULL DEFAULT 'SUBMITTED',
    submitted_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES unit(tenant_id, unit_id),
    FOREIGN KEY (tenant_id, department_id) REFERENCES department(tenant_id, department_id),
    FOREIGN KEY (tenant_id, submitter_id) REFERENCES app_user(tenant_id, user_id)
);

-- Cross-cutting reliability, files, notifications and integration processing
CREATE TABLE idempotency_record (
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    response_status integer,
    response_body jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    PRIMARY KEY (tenant_id, idempotency_key)
);

CREATE TABLE outbox_event (
    event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    event_type text NOT NULL,
    aggregate_type text NOT NULL,
    aggregate_id uuid NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0)
);

CREATE TABLE integration_job (
    job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    integration_type text NOT NULL,
    operation text NOT NULL,
    reference_type text,
    reference_id uuid,
    status text NOT NULL DEFAULT 'QUEUED',
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE stored_file (
    file_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    storage_key text NOT NULL,
    file_name text NOT NULL,
    content_type text NOT NULL,
    sha256 text NOT NULL,
    uploaded_by uuid,
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, storage_key),
    FOREIGN KEY (tenant_id, uploaded_by) REFERENCES app_user(tenant_id, user_id)
);

CREATE TABLE notification (
    notification_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenant(tenant_id),
    recipient_user_id uuid NOT NULL,
    channel text NOT NULL CHECK (channel IN ('IN_APP','EMAIL','WHATSAPP')),
    template_code text NOT NULL,
    reference_type text,
    reference_id uuid,
    status text NOT NULL DEFAULT 'QUEUED',
    sent_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, recipient_user_id) REFERENCES app_user(tenant_id, user_id)
);

-- Frequently used indexes. Add workload-specific indexes after query profiling.
CREATE INDEX ix_lot_fefo ON lot (tenant_id, item_id, store_id, expiry_at, received_at) WHERE status = 'AVAILABLE';
CREATE INDEX ix_stock_ledger_lot_time ON stock_ledger (tenant_id, lot_id, occurred_at);
CREATE INDEX ix_stock_balance_lookup ON stock_balance (tenant_id, item_id, store_id, lot_id);
CREATE INDEX ix_stock_ledger_reference ON stock_ledger (tenant_id, reference_type, reference_id);
CREATE INDEX ix_approval_pending ON approval_instance (tenant_id, status, current_step, submitted_at) WHERE status = 'PENDING';
CREATE INDEX ix_approval_action_actor ON approval_action (tenant_id, actor_user_id, decided_at DESC);
CREATE INDEX ix_po_schedule_due ON po_schedule (tenant_id, due_date) WHERE received_qty < scheduled_qty;
CREATE INDEX ix_grn_status ON grn (tenant_id, unit_id, status, received_at DESC);
CREATE INDEX ix_so_schedule_due ON so_schedule (tenant_id, due_date) WHERE fulfilled_qty < scheduled_qty;
CREATE INDEX ix_prod_lot_stage ON prod_lot (tenant_id, unit_id, route_id, current_stage_id, status);
CREATE INDEX ix_compound_batch_ready ON compound_batch (tenant_id, unit_id, compound_id, mixed_at) WHERE qa_status = 'PASSED' AND remaining_kg > 0;
CREATE INDEX ix_outbox_unpublished ON outbox_event (created_at) WHERE published_at IS NULL;
CREATE INDEX ix_integration_job_retry ON integration_job (next_attempt_at, created_at) WHERE status IN ('QUEUED','RETRY');
CREATE INDEX ix_audit_entity ON audit_log (tenant_id, entity_type, entity_id, occurred_at DESC);

-- Row-level security scaffold. Application sets app.tenant_id for each transaction.
CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS uuid
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION prevent_immutable_row_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only; % is not allowed', TG_TABLE_NAME, TG_OP;
END;
$$;

CREATE TRIGGER stock_ledger_append_only
BEFORE UPDATE OR DELETE ON stock_ledger
FOR EACH ROW EXECUTE FUNCTION prevent_immutable_row_change();

CREATE TRIGGER audit_log_append_only
BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION prevent_immutable_row_change();

DO $$
DECLARE r record;
BEGIN
    FOR r IN
        SELECT table_schema, table_name
        FROM information_schema.columns
        WHERE table_schema = 'erp' AND column_name = 'tenant_id'
        GROUP BY table_schema, table_name
    LOOP
        EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', r.table_schema, r.table_name);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I.%I USING (tenant_id = erp.current_tenant_id()) WITH CHECK (tenant_id = erp.current_tenant_id())', r.table_schema, r.table_name);
    END LOOP;
END $$;

-- Derived compound state. Final application logic must also lock rows when consuming stock.
CREATE VIEW compound_batch_status AS
SELECT b.batch_id,
       b.tenant_id,
       b.unit_id,
       b.compound_id,
       b.batch_no,
       b.total_kg,
       b.remaining_kg,
       b.mixed_at,
       CASE
         WHEN b.qa_status = 'FAILED' THEN 'REJECTED'
         WHEN b.remaining_kg <= 0 THEN 'CONSUMED'
         WHEN b.mixed_at IS NULL THEN 'MIXING'
         WHEN now() < b.mixed_at + make_interval(secs => (c.maturation_min_hours * 3600)::double precision) THEN 'MATURING'
         WHEN now() > b.mixed_at + make_interval(secs => (c.maximum_age_hours * 3600)::double precision) THEN 'EXPIRED'
         WHEN b.qa_status <> 'PASSED' THEN 'AWAITING_QA'
         ELSE 'READY'
       END AS derived_status
FROM compound_batch b
JOIN compound c ON c.tenant_id = b.tenant_id AND c.compound_id = b.compound_id;

-- No demo/company placeholder masters are inserted here. Seed roles, permissions, routes,
-- compounds, settings, units and stores from approved configuration/migration files.

COMMIT;
