export type Me = {
  user_id: string;
  email: string;
  display_name: string;
  role: string;
  roles: string[];
  permissions: string[];
  modules: string[];
  all_plants: boolean;
  unit_ids: string[];
  mfa_required: boolean;
  two_factor_enabled: boolean;
  access_granted: boolean;
};

export type BusinessRule = {
  key: string;
  rule: string;
  description: string;
  kind: string;
  options: string[];
  nullable: boolean;
  value: unknown;
  default: unknown;
  is_default: boolean;
  status: string;
  changed_at: string | null;
  changed_by: string | null;
};

export type ApprovalCount = { pending: number; overdue: number };
export type ApprovalScope = "mine" | "all";
export type ApprovalState = "PENDING" | "APPROVED" | "REJECTED" | "CANCELLED";
export type ApprovalSummary = {
  approval_id: string;
  document_type: string;
  document_id: string;
  title: string | null;
  amount: string | null;
  status: ApprovalState;
  current_step: number;
  step_label: string | null;
  step_role: string | null;
  submitted_by_name: string | null;
  submitted_at: string;
  due_at: string | null;
  overdue: boolean;
};
export type ApprovalStep = {
  seq: number;
  label: string;
  role: string | null;
  state: "APPROVED" | "REJECTED" | "SKIPPED" | "CURRENT" | "WAITING";
  decided_by: string | null;
  decision: string | null;
  decided_at: string | null;
  note: string | null;
};
export type ApprovalDetail = ApprovalSummary & {
  rule: string;
  steps: ApprovalStep[];
};
export type NotificationCount = { unread: number };
export type PublicApprovalLink = {
  title: string | null;
  document_type: string;
  amount: string | null;
  step_label: string | null;
  requested_by: string | null;
  expires_at: string;
};
export type PublicApprovalDecision = { status: string };
export type InAppNotification = {
  notification_id: string;
  template_code: string;
  subject: string | null;
  body: string | null;
  reference_type: string | null;
  reference_id: string | null;
  created_at: string;
  read_at: string | null;
};
export type Unit = {
  unit_id: string;
  code: string;
  name: string;
  gstin: string | null;
  state_code: string | null;
  active: boolean;
};
export type UnitAddress = { address_line: string | null; city: string | null; pincode: string | null };
export type UnitCreate = { code: string; name: string; gstin: string | null; state_code: string | null };
export type UnitUpdate = { name: string; gstin: string | null; state_code: string | null; active: boolean };
export type Department = {
  department_id: string;
  code: string;
  name: string;
  unit_id: string | null;
  parent_department_id: string | null;
  head_role_code: string | null;
  active: boolean;
};
export type DepartmentCreate = Omit<Department, "department_id" | "active">;
export type DepartmentUpdate = Omit<Department, "department_id" | "code">;
export type StoreType = "RAW_MATERIAL" | "WIP" | "FINISHED_GOODS" | "SPARES" | "OTHER";
export type Store = {
  store_id: string;
  unit_id: string;
  code: string;
  name: string;
  store_type: StoreType;
  active: boolean;
};
export type StoreCreate = Omit<Store, "store_id" | "active">;
export type StoreUpdate = Omit<Store, "store_id" | "unit_id" | "code">;
export type PaymentTerm = { payment_term_id: string; code: string; name: string; due_days: number };
export type PaymentTermCreate = { code: string; name: string; due_days: number };
export type PaymentTermUpdate = { name: string; due_days: number };
export type TaxCode = { tax_code_id: string; code: string; description: string; gst_rate: string; active: boolean };
export type TaxCodeCreate = { code: string; description: string; gst_rate: string };
export type TaxCodeUpdate = { description: string; gst_rate: string; active: boolean };
export type ItemCategory = { category_id: string; code: string; name: string; parent_category_id: string | null };
export type ItemCategoryCreate = { code: string; name: string; parent_category_id: string | null };
export type ItemCategoryUpdate = { name: string; parent_category_id: string | null };
export type UnitOfMeasure = { uom_id: string; code: string; name: string; decimal_places: number };
export type Customer = { customer_id: string; code: string; name: string; gstin: string | null; payment_term_id: string | null; credit_limit: string | null; active: boolean };
export type CustomerCreate = { code: string; name: string; gstin: string | null; payment_term_id: string | null; credit_limit: string | null };
export type CustomerUpdate = { name: string; gstin: string | null; payment_term_id: string | null; credit_limit: string | null; active: boolean };
export type CustomerAddress = { address_line: string | null; city: string | null; pincode: string | null };
export type CustomerCreditPosition = {
  customer_code: string;
  credit_limit: string;
  open_orders: string;
  invoiced: string;
  received: string;
  unapplied_receipts: string;
  overdue_amount: string;
  overdue_invoices: number;
  exposure: string;
  available: string;
  over_limit: boolean;
  blocks: boolean;
};
export type CustomerStatementLine = { date: string; kind: string; reference: string; debit: string; credit: string; balance: string };
export type CustomerStatement = { customer_id: string; customer_code: string; unit_id: string; lines: CustomerStatementLine[]; balance: string };
export type VendorStatus = "PENDING_APPROVAL" | "APPROVED" | "REJECTED" | "INACTIVE";
export type Vendor = { vendor_id: string; code: string; name: string; gstin: string | null; pan: string | null; msme_flag: boolean; payment_term_id: string | null; status: VendorStatus };
export type VendorCreate = { code: string; name: string; gstin: string | null; pan: string | null; msme_flag: boolean; payment_term_id: string | null };
export type VendorUpdate = { name: string; gstin: string | null; pan: string | null; msme_flag: boolean; payment_term_id: string | null };
export type VendorBankAccount = { vendor_id: string; account_holder: string; account_no_masked: string; ifsc: string; bank_name: string | null };
export type VendorBankAccountUpdate = { account_holder: string; account_no: string; ifsc: string; bank_name: string | null };
export type ItemType = "RM" | "WIP" | "FG" | "SPARE" | "CONSUMABLE" | "CAPITAL" | "OTHER";
export type Item = { item_id: string; code: string; name: string; item_type: ItemType; uom_code: string; category_id: string | null; hsn_code: string | null; shelf_life_days: number | null; route_code: string | null; active: boolean };
export type ItemCreate = { code: string; name: string; item_type: ItemType; uom_code: string; category_id: string | null; hsn_code: string | null; shelf_life_days: number | null; route_code: string | null };
export type ItemUpdate = { name: string; category_id: string | null; hsn_code: string | null; shelf_life_days: number | null; route_code: string | null; active?: boolean };
export type ItemPolicy = { unit_id: string; unit_code: string; reorder_point: string; reorder_qty: string; default_store_id: string | null };
export type ItemPolicyUpdate = Pick<ItemPolicy, "reorder_point" | "reorder_qty" | "default_store_id">;
export type ItemSpecification = { name: string; unit: string | null; target_value: string | null; min_value: string | null; max_value: string | null; text_value: string | null; note: string | null };
export type ItemSpecificationUpdate = Omit<ItemSpecification, "name">;
export type ItemCustomerPart = { xref_id: string; customer_id: string; customer_code: string; customer_part_no: string };
export type BomLine = { line_no: number; component_item_id: string; component_code: string; component_name: string; uom_code: string; quantity_per: string; scrap_factor: string };
export type ItemBom = { bom_id: string; item_id: string; item_code: string; item_name: string; version: number; status: string; valid_from: string; valid_to: string | null; approval_id: string | null; lines: BomLine[] };
export type ProductionStage = { sequence_no: number; stage_code: string; stage_name: string; is_qa_gate: boolean; is_end: boolean };
export type ProductionRoute = { route_id: string; code: string; name: string; version: number; active: boolean; stages: ProductionStage[] };
export type ProductionStageInput = Omit<ProductionStage, "sequence_no">;
export type ItemEngineeringChange = { version: number; from_version: number | null; bom_id: string; status: string; reason: string; effective_from: string; requested_by: string | null; requested_at: string; approval_id: string | null; approval_status: string | null };
export type Page<T> = { items: T[]; total: number; limit: number; offset: number };
export type IndentType = "BOM" | "CONSUMABLE" | "CAPITAL";
export type IndentSummary = {
  indent_id: string; indent_no: string; unit_id: string; unit_code: string; department_id: string | null;
  requester_id: string; requester_name: string; indent_type: IndentType; status: string;
  needed_by: string | null; created_at: string; approval_id: string | null;
};
export type IndentLineInput = { item_id: string; requested_qty: string; purpose: string | null; capital_description: string | null; capital_value: string | null };
export type IndentLine = IndentLineInput & { line_no: number; item_code: string; item_name: string; uom_code: string };
export type Indent = IndentSummary & { capital_total: string | null; lines: IndentLine[] };
export type IndentCreate = { unit_id: string; department_id: string | null; indent_type: IndentType; needed_by: string | null; lines: IndentLineInput[] };
export type RfqSummary = { rfq_id: string; rfq_no: string; unit_id: string; unit_code: string; status: string; response_due_at: string | null; created_by_name: string; created_at: string };
export type RfqLineInput = { item_id: string; quantity: string; required_by: string | null };
export type RfqLine = RfqLineInput & { rfq_line_id: string; line_no: number; item_code: string; item_name: string; uom_code: string };
export type RfqInvitation = { vendor_id: string; vendor_code: string; vendor_name: string; response_status: string; invited_at: string; quotes_received: number };
export type Rfq = RfqSummary & { lines: RfqLine[]; invitations: RfqInvitation[] };
export type RfqCreate = { unit_id: string; response_due_at: string | null; lines: RfqLineInput[]; vendor_ids: string[] };
export type RfqUpdate = { response_due_at: string | null; lines: RfqLineInput[] };
export type RfqQuote = { vendor_id: string; quote_reference: string | null; valid_until: string | null; lines: Array<{ rfq_line_id: string; unit_price: string; quoted_qty: string; lead_days: number | null }> };
export type RfqAward = { vendor_id: string; due_date: string; tax_code_id: string | null; payment_term_id: string | null };
export type RfqOffer = { vendor_id: string; vendor_code: string; unit_price: string; quoted_qty: string; lead_days: number | null; line_total: string; valid_until: string | null; expired: boolean; covers_quantity: boolean; is_lowest: boolean };
export type RfqCompareLine = { rfq_line_id: string; line_no: number; item_code: string; quantity: string; offers: RfqOffer[] };
export type RfqVendorTotal = { vendor_id: string; vendor_code: string; vendor_name: string; lines_quoted: number; covers_all_lines: boolean; has_expired_offer: boolean; total: string; max_lead_days: number | null };
export type RfqComparison = { rfq_id: string; rfq_no: string; lines: RfqCompareLine[]; vendors: RfqVendorTotal[]; cheapest_vendor_id: string | null };
export type PoSummary = { po_id: string; po_no: string; unit_id: string; unit_code: string; vendor_id: string; vendor_code: string; vendor_name: string; status: string; currency_code: string; payment_term_id: string | null; rfq_id: string | null; rfq_no: string | null; created_by_name: string; created_at: string; approval_id: string | null };
export type PoLine = { po_line_id: string; line_no: number; item_id: string; item_code: string; item_name: string; uom_code: string; ordered_qty: string; unit_price: string; tax_code_id: string | null; tax_code: string | null; gst_rate: string | null; net: string; gst: string; gross: string; received_qty: string; schedule: Array<{ due_date: string; scheduled_qty: string; received_qty: string }> };
export type Po = PoSummary & { net_total: string; gst_total: string; gross_total: string; indents: Array<{ indent_id: string; indent_no: string }>; lines: PoLine[] };
export type PoLateLine = { po_id: string; po_no: string; vendor_code: string; vendor_name: string; item_code: string; due_date: string; days_late: number; pending_qty: string };
export type PoHistory = { events: Array<{ occurred_at: string; action: string; actor: string | null; actor_role_code: string | null; details: Record<string, unknown> }>; approval: ({ status: string; [key: string]: unknown }) | null };
export type PoLineInput = { item_id: string; ordered_qty: string; unit_price: string; tax_code_id: string | null; due_date: string | null; schedule: Array<{ due_date: string; quantity: string }> };
export type PoCreate = { unit_id: string; vendor_id: string; payment_term_id: string | null; rfq_id: string | null; indent_ids: string[]; lines: PoLineInput[] };
export type PoFromIndents = { indent_ids: string[]; vendor_id: string; payment_term_id: string | null; due_date: string | null; prices: Array<{ item_id: string; unit_price: string; tax_code_id: string | null }> };
export type PoReceivingLine = { po_line_id: string; line_no: number; item_code: string; ordered_qty: string; received_qty: string; pending_qty: string; max_receivable_qty: string };
export type PoUpdate = { payment_term_id: string | null; lines: PoLineInput[] };
export type GrnSummary = { grn_id: string; grn_no: string; unit_id: string; unit_code: string; po_id: string; po_no: string; vendor_code: string; supplier_dc_no: string | null; received_at: string; status: string };
export type GrnLine = { grn_line_id: string; po_line_id: string; item_id: string; item_code: string; store_id: string; store_code: string; lot_id: string; lot_no: string; lot_status: string; expiry_at: string | null; received_qty: string; accepted_qty: string; rejected_qty: string; inspected: boolean };
export type Grn = GrnSummary & { lines: GrnLine[] };
export type GrnCreate = { po_id: string; supplier_dc_no: string; received_at: string | null; lines: Array<{ po_line_id: string; store_id: string; received_qty: string; supplier_lot_no: string | null }> };
export type StockRow = { lot_id: string; lot_no: string; lot_status: string; item_id: string; item_code: string; item_name: string; uom_code: string; store_id: string; store_code: string; unit_id: string; unit_code: string; received_at: string; expiry_at: string | null; expired: boolean; on_hand: string; reserved: string; available: string };
export type ReservationReferenceType = "WORK_ORDER" | "SALES_ORDER" | "DISPATCH" | "OTHER";
export type Reservation = { reservation_id: string; lot_id: string; lot_no: string; item_code: string; store_code: string; quantity: string; reference_type: ReservationReferenceType; reference: string; status: string; created_at: string };
export type ReservationReference = { reference_type: ReservationReferenceType; reference: string };
export type ReservationCreate = ReservationReference & { store_id: string; item_id: string; quantity: string };
export type TransferSummary = { transfer_id: string; transfer_no: string; status: string; from_store_id: string; from_store_code: string; from_unit_id: string; to_store_id: string; to_store_code: string; to_unit_id: string; dispatched_at: string | null; received_at: string | null };
export type TransferLine = { transfer_line_id: string; item_id: string; item_code: string; lot_id: string; lot_no: string; quantity: string; unit_cost: string | null };
export type TransferOut = TransferSummary & { lines: TransferLine[] };
export type TransferCreate = { from_store_id: string; to_store_id: string; lines: { lot_id: string; quantity: string }[] };
export type CountSummary = { count_id: string; count_no: string; unit_id: string; store_id: string; store_code: string; period_start: string; status: string; started_at: string; submitted_by_name: string | null };
export type CountLine = { count_line_id: string; item_id: string; item_code: string; lot_id: string; lot_no: string; system_qty: string; counted_qty: string | null; variance: string | null; counted_at: string | null };
export type CountOut = CountSummary & { lines: CountLine[] };
export type CompoundRecipeLine = { recipe_line_id: string; line_no: number; item_id: string; item_code: string; item_name: string; phr: string };
export type Compound = { compound_id: string; code: string; name: string; lane: string; maturation_min_hours: string; maximum_age_hours: string; hardness_min: string | null; hardness_max: string | null; output_item_id: string | null; active: boolean; recipe: CompoundRecipeLine[] };
export type CompoundCreate = { code: string; name: string; lane: "RUBBER" | "EXTRUSION"; maturation_min_hours: string; maximum_age_hours: string; hardness_min: string | null; hardness_max: string | null; output_item_id: string | null; recipe: { item_id: string; phr: string }[] };
export type CompoundPatch = Partial<Omit<CompoundCreate, "code" | "lane">> & { active?: boolean };
export type CompoundBatchSummary = { batch_id: string; batch_no: string; unit_id: string; compound_id: string; compound_code: string; compound_name: string; status: string; total_kg: string; remaining_kg: string; weighed_at: string | null; mixed_at: string | null; age_hours: string | null; maturation_min_hours: string; maximum_age_hours: string; qa_status: string; hardness: string | null };
export type CompoundBatch = CompoundBatchSummary & { qa_note: string | null; qa_at: string | null; weighing: { item_code: string; item_name: string; lot_no: string; target_qty: string; actual_qty: string; variance: string }[] };
export type CompoundBatchCreate = { compound_id: string; store_id: string; batch_kg: string; lines?: { recipe_line_id: string; actual_qty: string }[] };
export type ProductionLotSummary = { prod_lot_id: string; lot_no: string; unit_id: string; work_order_id: string; work_order_no: string; item_id: string; item_code: string; route_code: string; stage_code: string; stage_name: string; is_qa_gate: boolean; is_end: boolean; quantity: string; status: string; created_at: string };
export type ProductionLotCreate = { work_order_id: string; quantity: string; store_id: string | null };
export type ProductionLotSource = { consumption_type: string; quantity: string; source: string; item_code: string | null; consumed_at: string };
export type ProductionLotMove = { from_stage: string | null; to_stage: string; actor: string; quantity_in: string; quantity_out: string; rejects: string; moved_at: string; notes: string | null };
export type ProductionLotPacking = { label_code: string; packed_qty: string; packed_at: string; fg_store_id: string; fg_lot_no: string | null };
export type ProductionLot = ProductionLotSummary & { sources: ProductionLotSource[]; moves: ProductionLotMove[]; packing: ProductionLotPacking | null };
export type ProductionLotTrace = { lot_no: string; quantity: string; status: string; item_code: string; work_order_no: string; raw_material: Array<{ lot_no: string; item_code: string; quantity: string }>; compound_batches: Array<{ batch_no: string; compound_code: string; quantity: string; raw_material: Array<{ lot_no: string; item_code: string; quantity: string }> }>; metal_lots: Array<ProductionLotTrace & { quantity_used: string }>; packing: ProductionLotPacking | null };
export type Machine = { machine_id: string; unit_id: string; code: string; name: string; machine_type: string | null; active: boolean; hourly_rate: string; labour_per_hour: string };
export type Mould = { mould_id: string; unit_id: string; code: string; part_item_id: string; part_code: string; shots_limit: number | null; active: boolean };
export type MachineCreate = { unit_id: string; code: string; name: string; machine_type: string | null; hourly_rate: string; labour_per_hour: string };
export type MachinePatch = Partial<Pick<Machine, "name" | "machine_type" | "hourly_rate" | "labour_per_hour" | "active">>;
export type MouldCreate = { unit_id: string; code: string; part_item_id: string; shots_limit: number | null };
export type MouldPatch = Partial<Pick<Mould, "shots_limit" | "active">>;
export type WorkOrderSummary = { work_order_id: string; work_order_no: string; unit_id: string; item_id: string; item_code: string; item_name: string; bom_id: string; route_id: string; route_code: string; planned_qty: string; started_qty: string; packed_qty: string; status: string; planned_start: string | null; planned_finish: string | null };
export type WorkOrder = WorkOrderSummary & { bom_version: number; materials: { component_item_id: string; component_code: string; component_name: string; kind: string; quantity_per: string; scrap_factor: string; required_qty: string }[] };
export type WorkOrderCreate = { unit_id: string; item_id: string; planned_qty: string; planned_start?: string | null; planned_finish?: string | null };
export type WorkOrderMaterialVariance = { component_code: string; required: string; consumed: string; returned: string; variance: string };
export type ShiftLoss = { kind: "REJECT" | "DOWNTIME"; reason: string; quantity: string; minutes: number };
export type ShiftEntry = { shift_entry_id: string; unit_id: string; work_order_id: string; work_order_no: string; machine_id: string; machine_code: string; operator: string; work_date: string; shift_code: "A" | "B" | "C"; good_qty: string; reject_qty: string; downtime_minutes: number; mould_code: string | null; shots: number | null; losses: ShiftLoss[] };
export type ShiftEntryCreate = { work_order_id: string; machine_id: string; work_date: string; shift_code: "A" | "B" | "C"; good_qty: string; rejects: Array<{ reason: string; quantity: string }>; downtime: Array<{ reason: string; minutes: number }>; mould_id: string | null; shots: number | null };
export type MissingShiftEntry = { unit_id: string; machine_id: string; machine_code: string; work_date: string; shift_code: "A" | "B" | "C" };
export type ManufacturingWip = { route_code: string; stage_code: string; stage_name: string; item_code: string; lots: number; quantity: string };
export type ManufacturingOee = { machine_id: string; machine_code: string; shifts: number; planned_minutes: number; downtime_minutes: number; availability: string; good_qty: string; reject_qty: string; quality: string | null; performance: string | null };
export type QualityQueueKind = "GRN_LINE" | "COMPOUND_BATCH" | "PROD_LOT";
export type QualityQueueRow = { subject_type: QualityQueueKind; subject_id: string; reference: string; item_code: string | null; quantity: string; stage: string | null; waiting_since: string; unit_id: string; inspected: boolean };
export type InspectionPlanKind = "INCOMING" | "IN_PROCESS" | "FINAL";
export type InspectionPlanParameter = { parameter_code: string; name: string; unit: string | null; min_value: string | null; max_value: string | null; is_key: boolean };
export type InspectionPlan = { plan_id: string; item_id: string; item_code: string; kind: InspectionPlanKind; name: string; active: boolean; created_at: string; params: InspectionPlanParameter[] };
export type InspectionPlanCreate = { item_id: string; kind: InspectionPlanKind; name: string; params: InspectionPlanParameter[] };
export type InspectionSubjectType = "GRN_LINE" | "COMPOUND_BATCH" | "PROD_LOT" | "STOCK_LOT";
export type InspectionResult = "ACCEPT" | "PARTIAL" | "REJECT" | "HOLD";
export type InspectionSummary = { inspection_id: string; inspection_no: string; kind: InspectionPlanKind | null; subject_type: InspectionSubjectType | null; subject_id: string | null; result: InspectionResult; source: string; inspected_by_name: string; inspected_at: string; notes: string | null; unit_id: string | null };
export type InspectionMeasurement = { parameter_code: string; measured_value: string | null; unit: string | null; min_value: string | null; max_value: string | null; result: string | null };
export type Inspection = InspectionSummary & { plan_name: string | null; instrument_code: string | null; measurements: InspectionMeasurement[] };
export type InspectionCreate = { kind: InspectionPlanKind; subject_type: InspectionSubjectType; subject_id: string; result: InspectionResult; notes: string | null; instrument_id: string | null; measurements: Array<{ parameter_code: string; value: string; unit: string | null }> };
export type QualityInstrument = { instrument_id: string; unit_id: string; code: string; name: string; active: boolean; last_calibrated_at: string | null; due_at: string | null; last_result: string | null; calibration_status: string; days_to_due: number | null };
export type QualityHoldType = "STOCK_LOT" | "COMPOUND_BATCH" | "PROD_LOT";
export type QualityHold = { hold_id: string; unit_id: string; subject_type: QualityHoldType; subject_id: string; subject_label: string | null; reason: string; ncr_id: string | null; placed_by_name: string; placed_at: string; released_by_name: string | null; released_at: string | null; release_note: string | null; active: boolean };
export type CompoundAvailability = { compound_id: string; ready_kg: string; batches: CompoundBatchSummary[] };
export type InventoryValuation = { unit_id: string; unit_code: string; item_id: string; item_code: string; quantity: string; value: string; average_cost: string | null };
export type InventoryExpiring = { lot_id: string; lot_no: string; item_code: string; store_code: string; expiry_at: string; on_hand: string; days_left: number };
export type InventoryReorder = { unit_id: string; unit_code: string; item_id: string; item_code: string; item_name: string; available: string; reorder_point: string; suggested_qty: string };
export type InventoryReconciliation = { ok: boolean; checked_at: string; lots_checked: number; problems: { kind: string; detail: string }[] };
export type LotTrace = { lot_id: string; lot_no: string; item_code: string; store_code: string; status: string; received_at: string; expiry_at: string | null; on_hand: string; origin: { grn_no: string; supplier_dc_no: string | null; po_no: string; vendor: string; received_at: string; received_qty: string; accepted_qty: string; rejected_qty: string } | null; moves: Array<{ movement_type: string; quantity: string; reference_type: string; reference_id: string; document_no: string | null; occurred_at: string }>; links: Array<{ direction: string; lot_id: string | null; lot_no: string; store_code: string; transfer_no: string; quantity: string }> };
export type IssueInput = { store_id: string; lines: Array<{ item_id: string; quantity: string }> };
export type IssuePlan = { store_id: string; rule: string; can_issue: boolean; lines: Array<{ item_id: string; item_code: string; requested: string; picks: Array<{ lot_id: string; lot_no: string; expiry_at: string | null; received_at: string; quantity: string }>; shortfall: string; blocked: Array<{ lot_id: string; lot_no: string; expiry_at: string | null; quantity: string; reason: string }> }> };
export type IssueSummary = { issue_id: string; issue_no: string; unit_id: string; store_id: string; store_code: string; purpose: string; reference: string | null; issued_by_name: string | null; issued_at: string };
export type IssueOut = IssueSummary & { total_cost: string; lines: Array<{ item_code: string; lot_no: string; quantity: string; cost: string }> };

type ErrorBody = {
  detail?: string | Array<{ loc?: Array<string | number>; msg?: string }>;
  request_id?: string;
};

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number;
  readonly requestId?: string;
  readonly fieldErrors: Array<{ field: string; message: string }>;

  constructor(
    status: number,
    message: string,
    requestId?: string,
    fieldErrors: Array<{ field: string; message: string }> = [],
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
    this.fieldErrors = fieldErrors;
  }
}

function safeMessage(status: number, body: ErrorBody): string {
  if (typeof body.detail === "string" && body.detail.trim()) return body.detail;
  if (status === 401) return "Your session has expired. Please sign in again.";
  if (status === 403) return "You do not have permission to open this workspace.";
  if (status === 404) return "The requested record could not be found or is outside your access scope.";
  if (status === 409) return "This record changed while you were working. Refresh it before trying again.";
  if (status === 429) return "Too many requests were made. Please wait and try again.";
  if (status >= 500) return "The server could not complete this request.";
  return "The request could not be completed.";
}

async function parseError(response: Response): Promise<ApiError> {
  const body = await response.json().catch(() => ({} as ErrorBody)) as ErrorBody;
  const requestId = response.headers.get("X-Request-ID") ?? body.request_id ?? undefined;
  const fieldErrors = Array.isArray(body.detail)
    ? body.detail.map(item => ({
      field: (item.loc ?? []).slice(1).join("."),
      message: item.msg ?? "Invalid value",
    }))
    : [];
  return new ApiError(response.status, safeMessage(response.status, body), requestId, fieldErrors);
}

export async function erpRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_URL}/api/v1${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function publicErpRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_URL}/api/v1${path}`, {
    ...init,
    headers,
    credentials: "omit",
  });
  if (!response.ok) throw await parseError(response);
  return response.json() as Promise<T>;
}

export const fetchMe = () => erpRequest<Me>("/me");
export const fetchSettings = () => erpRequest<BusinessRule[]>("/settings");
export const fetchApprovalCount = () => erpRequest<ApprovalCount>("/approvals/count");
export const fetchApprovals = (
  query: { scope: ApprovalScope; state: ApprovalState; limit: number; offset: number },
  signal?: AbortSignal,
) => {
  const params = new URLSearchParams({
    scope: query.scope,
    state: query.state,
    limit: String(query.limit),
    offset: String(query.offset),
  });
  return erpRequest<Page<ApprovalSummary>>(`/approvals?${params.toString()}`, { signal });
};
export const fetchApprovalDetail = (approvalId: string, signal?: AbortSignal) =>
  erpRequest<ApprovalDetail>(`/approvals/${encodeURIComponent(approvalId)}`, { signal });
export const decideApproval = (
  approvalId: string,
  body: { decision: "APPROVE" | "REJECT"; note: string | null; step: number },
  idempotencyKey: string,
  signal?: AbortSignal,
) => erpRequest<ApprovalDetail>(`/approvals/${encodeURIComponent(approvalId)}/decide`, {
  method: "POST",
  headers: { "Idempotency-Key": idempotencyKey },
  body: JSON.stringify(body),
  signal,
});
export const fetchNotificationCount = () => erpRequest<NotificationCount>("/notifications/count");
export const fetchNotifications = (unread: boolean, limit = 30, signal?: AbortSignal) => {
  const params = new URLSearchParams({ unread: String(unread), limit: String(limit) });
  return erpRequest<InAppNotification[]>(`/notifications?${params.toString()}`, { signal });
};
export const markNotificationRead = (notificationId: string) =>
  erpRequest<InAppNotification>(`/notifications/${encodeURIComponent(notificationId)}/read`, { method: "POST" });
export const markAllNotificationsRead = () =>
  erpRequest<NotificationCount>("/notifications/read-all", { method: "POST" });
export const fetchPublicApproval = (token: string, signal?: AbortSignal) =>
  publicErpRequest<PublicApprovalLink>(`/public/approval-links/${encodeURIComponent(token)}`, { signal });
export const decidePublicApproval = (
  token: string,
  decision: "APPROVE" | "REJECT",
  note: string | null,
  signal?: AbortSignal,
) => publicErpRequest<PublicApprovalDecision>(`/public/approval-links/${encodeURIComponent(token)}/decide`, {
  method: "POST",
  body: JSON.stringify({ decision, note }),
  signal,
});
export const fetchUnitsPage = (offset = 0, limit = 200) => erpRequest<Page<Unit>>(`/org/units?active=true&limit=${limit}&offset=${offset}`);
export const listUnits = (offset = 0, limit = 20, signal?: AbortSignal) =>
  erpRequest<Page<Unit>>(`/org/units?limit=${limit}&offset=${offset}`, { signal });
export const fetchUnit = (unitId: string, signal?: AbortSignal) =>
  erpRequest<Unit>(`/org/units/${encodeURIComponent(unitId)}`, { signal });
export const createUnit = (body: UnitCreate) =>
  erpRequest<Unit>("/org/units", { method: "POST", body: JSON.stringify(body) });
export const updateUnit = (unitId: string, body: UnitUpdate) =>
  erpRequest<Unit>(`/org/units/${encodeURIComponent(unitId)}`, { method: "PATCH", body: JSON.stringify(body) });
export const setUnitAddress = (unitId: string, body: { address_line: string; city: string; pincode: string }) =>
  erpRequest<UnitAddress>(`/org/units/${encodeURIComponent(unitId)}/address`, { method: "PUT", body: JSON.stringify(body) });
export const listDepartmentsPage = (
  query: { active: boolean | null; unitId: string | null; limit: number; offset: number },
  signal?: AbortSignal,
) => {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.active !== null) params.set("active", String(query.active));
  if (query.unitId) params.set("unit_id", query.unitId);
  return erpRequest<Page<Department>>(`/org/departments?${params.toString()}`, { signal });
};
export async function fetchAllDepartments(active: boolean | null, unitId: string | null, signal?: AbortSignal) {
  const departments: Department[] = [];
  let offset = 0;
  let total = Number.POSITIVE_INFINITY;
  while (departments.length < total) {
    const page = await listDepartmentsPage({ active, unitId, limit: 200, offset }, signal);
    total = page.total;
    if (page.items.length === 0) break;
    departments.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return departments;
}
export const fetchDepartment = (departmentId: string, signal?: AbortSignal) =>
  erpRequest<Department>(`/org/departments/${encodeURIComponent(departmentId)}`, { signal });
export const createDepartment = (body: DepartmentCreate) =>
  erpRequest<Department>("/org/departments", { method: "POST", body: JSON.stringify(body) });
export const updateDepartment = (departmentId: string, body: DepartmentUpdate) =>
  erpRequest<Department>(`/org/departments/${encodeURIComponent(departmentId)}`, { method: "PATCH", body: JSON.stringify(body) });
export const listStoresPage = (
  query: { active: boolean | null; unitId: string | null; storeType: StoreType | null; limit: number; offset: number },
  signal?: AbortSignal,
) => {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.active !== null) params.set("active", String(query.active));
  if (query.unitId) params.set("unit_id", query.unitId);
  if (query.storeType) params.set("store_type", query.storeType);
  return erpRequest<Page<Store>>(`/org/stores?${params.toString()}`, { signal });
};
export async function fetchAllStores(active: boolean | null, unitId: string | null, storeType: StoreType | null, signal?: AbortSignal) {
  const stores: Store[] = [];
  let offset = 0;
  let total = Number.POSITIVE_INFINITY;
  while (stores.length < total) {
    const page = await listStoresPage({ active, unitId, storeType, limit: 200, offset }, signal);
    total = page.total;
    if (page.items.length === 0) break;
    stores.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return stores;
}
export const fetchStore = (storeId: string, signal?: AbortSignal) =>
  erpRequest<Store>(`/org/stores/${encodeURIComponent(storeId)}`, { signal });
export const createStore = (body: StoreCreate) =>
  erpRequest<Store>("/org/stores", { method: "POST", body: JSON.stringify(body) });
export const updateStore = (storeId: string, body: StoreUpdate) =>
  erpRequest<Store>(`/org/stores/${encodeURIComponent(storeId)}`, { method: "PATCH", body: JSON.stringify(body) });

async function fetchAllPages<T>(path: string, signal?: AbortSignal): Promise<T[]> {
  const items: T[] = [];
  let offset = 0;
  let total = Number.POSITIVE_INFINITY;
  while (items.length < total) {
    const separator = path.includes("?") ? "&" : "?";
    const page = await erpRequest<Page<T>>(`${path}${separator}limit=200&offset=${offset}`, { signal });
    total = page.total;
    if (page.items.length === 0) break;
    items.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return items;
}

export const fetchAllPaymentTerms = (signal?: AbortSignal) => fetchAllPages<PaymentTerm>("/ref/payment-terms", signal);
export const createPaymentTerm = (body: PaymentTermCreate) =>
  erpRequest<PaymentTerm>("/ref/payment-terms", { method: "POST", body: JSON.stringify(body) });
export const updatePaymentTerm = (id: string, body: PaymentTermUpdate) =>
  erpRequest<PaymentTerm>(`/ref/payment-terms/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) });

export const fetchAllTaxCodes = (active: boolean | null, signal?: AbortSignal) =>
  fetchAllPages<TaxCode>(`/ref/tax-codes${active === null ? "" : `?active=${active}`}`, signal);
export const createTaxCode = (body: TaxCodeCreate) =>
  erpRequest<TaxCode>("/ref/tax-codes", { method: "POST", body: JSON.stringify(body) });
export const updateTaxCode = (id: string, body: TaxCodeUpdate) =>
  erpRequest<TaxCode>(`/ref/tax-codes/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) });

export const fetchAllItemCategories = (signal?: AbortSignal) => fetchAllPages<ItemCategory>("/ref/item-categories", signal);
export const createItemCategory = (body: ItemCategoryCreate) =>
  erpRequest<ItemCategory>("/ref/item-categories", { method: "POST", body: JSON.stringify(body) });
export const updateItemCategory = (id: string, body: ItemCategoryUpdate) =>
  erpRequest<ItemCategory>(`/ref/item-categories/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) });
export const fetchUnitsOfMeasure = (signal?: AbortSignal) => erpRequest<UnitOfMeasure[]>("/ref/uom", { signal });
export const listCustomers = (
  query: { q: string; active: boolean | null; limit: number; offset: number },
  signal?: AbortSignal,
) => {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.q) params.set("q", query.q);
  if (query.active !== null) params.set("active", String(query.active));
  return erpRequest<Page<Customer>>(`/parties/customers?${params.toString()}`, { signal });
};
export const createCustomer = (body: CustomerCreate) =>
  erpRequest<Customer>("/parties/customers", { method: "POST", body: JSON.stringify(body) });
export const fetchCustomer = (id: string, signal?: AbortSignal) =>
  erpRequest<Customer>(`/parties/customers/${encodeURIComponent(id)}`, { signal });
export const updateCustomer = (id: string, body: CustomerUpdate) =>
  erpRequest<Customer>(`/parties/customers/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) });
export const updateCustomerAddress = (id: string, body: CustomerAddress) =>
  erpRequest<CustomerAddress>(`/parties/customers/${encodeURIComponent(id)}/address`, { method: "PUT", body: JSON.stringify(body) });
export const fetchCustomerCredit = (id: string, signal?: AbortSignal) =>
  erpRequest<CustomerCreditPosition>(`/sales/customers/${encodeURIComponent(id)}/credit`, { signal });
export const fetchCustomerStatement = (id: string, unitId: string, signal?: AbortSignal) =>
  erpRequest<CustomerStatement>(`/finance/customers/${encodeURIComponent(id)}/statement?unit_id=${encodeURIComponent(unitId)}`, { signal });
export const listVendors = (
  query: { q: string; vendorStatus: VendorStatus | null; limit: number; offset: number },
  signal?: AbortSignal,
) => {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.q) params.set("q", query.q);
  if (query.vendorStatus) params.set("vendor_status", query.vendorStatus);
  return erpRequest<Page<Vendor>>(`/parties/vendors?${params.toString()}`, { signal });
};
export async function fetchAllApprovedVendors(signal?: AbortSignal): Promise<Vendor[]> {
  const vendors: Vendor[] = [];
  let offset = 0, total = Number.POSITIVE_INFINITY;
  while (vendors.length < total) {
    const page = await listVendors({ q: "", vendorStatus: "APPROVED", limit: 200, offset }, signal);
    total = page.total;
    if (!page.items.length) break;
    vendors.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return vendors;
}
export async function fetchAllVendors(signal?: AbortSignal): Promise<Vendor[]> {
  const vendors: Vendor[] = [];
  let offset = 0, total = Number.POSITIVE_INFINITY;
  while (vendors.length < total) {
    const page = await listVendors({ q: "", vendorStatus: null, limit: 200, offset }, signal);
    total = page.total;
    if (!page.items.length) break;
    vendors.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return vendors;
}
export const createVendor = (body: VendorCreate) =>
  erpRequest<Vendor>("/parties/vendors", { method: "POST", body: JSON.stringify(body) });
export const fetchVendor = (id: string, signal?: AbortSignal) =>
  erpRequest<Vendor>(`/parties/vendors/${encodeURIComponent(id)}`, { signal });
export const updateVendor = (id: string, body: VendorUpdate) =>
  erpRequest<Vendor>(`/parties/vendors/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) });
export const deactivateVendor = (id: string) =>
  erpRequest<Vendor>(`/parties/vendors/${encodeURIComponent(id)}/deactivate`, { method: "POST" });
export const fetchVendorBankAccount = (id: string, signal?: AbortSignal) =>
  erpRequest<VendorBankAccount>(`/finance/vendors/${encodeURIComponent(id)}/bank-account`, { signal });
export const updateVendorBankAccount = (id: string, body: VendorBankAccountUpdate) =>
  erpRequest<VendorBankAccount>(`/finance/vendors/${encodeURIComponent(id)}/bank-account`, { method: "PUT", body: JSON.stringify(body) });
export const listItems = (query: { q: string; itemType: ItemType | null; categoryId: string | null; active: boolean | null; limit: number; offset: number }, signal?: AbortSignal) => {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.q) params.set("q", query.q);
  if (query.itemType) params.set("item_type", query.itemType);
  if (query.categoryId) params.set("category_id", query.categoryId);
  if (query.active !== null) params.set("active", String(query.active));
  return erpRequest<Page<Item>>(`/product/items?${params.toString()}`, { signal });
};
export const createItem = (body: ItemCreate) =>
  erpRequest<Item>("/product/items", { method: "POST", body: JSON.stringify(body) });
const itemPath = (id: string) => `/product/items/${encodeURIComponent(id)}`;
export const fetchItem = (id: string, signal?: AbortSignal) => erpRequest<Item>(itemPath(id), { signal });
export const updateItem = (id: string, body: Partial<ItemUpdate>) => erpRequest<Item>(itemPath(id), { method: "PATCH", body: JSON.stringify(body) });
export const fetchItemPolicies = (id: string, signal?: AbortSignal) => erpRequest<ItemPolicy[]>(`${itemPath(id)}/policies`, { signal });
export const setItemPolicy = (id: string, unitId: string, body: ItemPolicyUpdate) => erpRequest<ItemPolicy>(`${itemPath(id)}/policies/${encodeURIComponent(unitId)}`, { method: "PUT", body: JSON.stringify(body) });
export const fetchItemSpecifications = (id: string, signal?: AbortSignal) => erpRequest<ItemSpecification[]>(`${itemPath(id)}/specs`, { signal });
export const setItemSpecification = (id: string, name: string, body: ItemSpecificationUpdate) => erpRequest<ItemSpecification>(`${itemPath(id)}/specs/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify(body) });
export const fetchItemCustomerParts = (id: string, signal?: AbortSignal) => erpRequest<ItemCustomerPart[]>(`${itemPath(id)}/customer-parts`, { signal });
export const addItemCustomerPart = (id: string, body: { customer_id: string; customer_part_no: string }) => erpRequest<ItemCustomerPart>(`${itemPath(id)}/customer-parts`, { method: "POST", body: JSON.stringify(body) });
export const fetchItemEffectiveBom = (id: string, signal?: AbortSignal) => erpRequest<ItemBom>(`${itemPath(id)}/effective-bom`, { signal });
export const fetchItemEngineeringChanges = (id: string, signal?: AbortSignal) => erpRequest<ItemEngineeringChange[]>(`${itemPath(id)}/engineering-changes`, { signal });
export const fetchItemWhereUsed = (id: string, signal?: AbortSignal) => erpRequest<ItemBom[]>(`${itemPath(id)}/where-used`, { signal });
export const listBomsPage = (query: { itemId: string | null; status: string | null; limit: number; offset: number }, signal?: AbortSignal) => {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.itemId) params.set("item_id", query.itemId);
  if (query.status) params.set("status", query.status);
  return erpRequest<Page<ItemBom>>(`/product/boms?${params.toString()}`, { signal });
};
export async function fetchAllBoms(itemId: string | null, status: string | null, signal?: AbortSignal) {
  const boms: ItemBom[] = [];
  let total = Number.POSITIVE_INFINITY;
  let offset = 0;
  while (boms.length < total) {
    const page = await listBomsPage({ itemId, status, limit: 200, offset }, signal);
    total = page.total;
    if (!page.items.length) break;
    boms.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return boms;
}
export async function fetchAllActiveItemsOfTypes(types: ItemType[], signal?: AbortSignal) {
  const groups = await Promise.all(types.map(async itemType => {
    const items: Item[] = [];
    let total = Number.POSITIVE_INFINITY;
    let offset = 0;
    while (items.length < total) {
      const page = await listItems({ q: "", itemType, categoryId: null, active: true, limit: 200, offset }, signal);
      total = page.total;
      if (!page.items.length) break;
      items.push(...page.items);
      offset = page.offset + page.items.length;
    }
    return items;
  }));
  return groups.flat().sort((a, b) => a.code.localeCompare(b.code));
}
export async function fetchAllItems(signal?: AbortSignal): Promise<Item[]> {
  const items: Item[] = [];
  let offset = 0, total = Number.POSITIVE_INFINITY;
  while (items.length < total) {
    const page = await listItems({ q: "", itemType: null, categoryId: null, active: null, limit: 200, offset }, signal);
    total = page.total;
    if (!page.items.length) break;
    items.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return items;
}
export const fetchAllMadeItems = (signal?: AbortSignal) => fetchAllActiveItemsOfTypes(["WIP", "FG"], signal);
export const fetchAllBomComponents = (signal?: AbortSignal) => fetchAllActiveItemsOfTypes(["RM", "WIP", "FG", "CONSUMABLE"], signal);
export const createBom = (body: { item_id: string; lines: Array<{ component_item_id: string; quantity_per: string; scrap_factor: string }> }) =>
  erpRequest<ItemBom>("/product/boms", { method: "POST", body: JSON.stringify(body) });
const bomPath = (id: string) => `/product/boms/${encodeURIComponent(id)}`;
export const fetchBom = (id: string, signal?: AbortSignal) => erpRequest<ItemBom>(bomPath(id), { signal });
export const updateBomLines = (id: string, lines: Array<{ component_item_id: string; quantity_per: string; scrap_factor: string }>) =>
  erpRequest<ItemBom>(`${bomPath(id)}/lines`, { method: "PUT", body: JSON.stringify({ lines }) });
export const submitBom = (id: string, body: { reason: string; effective_from: string }) =>
  erpRequest<ItemBom>(`${bomPath(id)}/submit`, { method: "POST", body: JSON.stringify(body) });
export const reviseBom = (id: string) => erpRequest<ItemBom>(`${bomPath(id)}/revise`, { method: "POST" });
export const listProductionRoutes = (active: boolean, signal?: AbortSignal) =>
  erpRequest<ProductionRoute[]>(`/product/routes?active=${String(active)}`, { signal });
export const findActiveProductionRoute = (code: string, signal?: AbortSignal) =>
  erpRequest<ProductionRoute>(`/product/routes/by-code/${encodeURIComponent(code)}`, { signal });
export const fetchProductionRoute = (id: string, signal?: AbortSignal) =>
  erpRequest<ProductionRoute>(`/product/routes/${encodeURIComponent(id)}`, { signal });
export const createProductionRoute = (body: { code: string; name: string; stages: ProductionStageInput[] }) =>
  erpRequest<ProductionRoute>("/product/routes", { method: "POST", body: JSON.stringify(body) });
export const reviseProductionRoute = (id: string, body: { name: string; stages: ProductionStageInput[] }) =>
  erpRequest<ProductionRoute>(`/product/routes/${encodeURIComponent(id)}/revise`, { method: "POST", body: JSON.stringify(body) });
export const retireProductionRoute = (id: string) =>
  erpRequest<ProductionRoute>(`/product/routes/${encodeURIComponent(id)}/retire`, { method: "POST" });
export const listIndentsPage = (query: { status: string | null; indentType: IndentType | null; unitId: string | null; mine: boolean; limit: number; offset: number }, signal?: AbortSignal) => {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.status) params.set("status", query.status);
  if (query.indentType) params.set("indent_type", query.indentType);
  if (query.unitId) params.set("unit_id", query.unitId);
  if (query.mine) params.set("mine", "true");
  return erpRequest<Page<IndentSummary>>(`/procurement/indents?${params.toString()}`, { signal });
};
export async function fetchAllIndents(query: Omit<Parameters<typeof listIndentsPage>[0], "limit" | "offset">, signal?: AbortSignal) {
  const indents: IndentSummary[] = [];
  let total = Number.POSITIVE_INFINITY;
  let offset = 0;
  while (indents.length < total) {
    const page = await listIndentsPage({ ...query, limit: 200, offset }, signal);
    total = page.total;
    if (!page.items.length) break;
    indents.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return indents;
}
export const createIndent = (body: IndentCreate) =>
  erpRequest<Indent>("/procurement/indents", { method: "POST", body: JSON.stringify(body) });
export const fetchIndent = (id: string, signal?: AbortSignal) =>
  erpRequest<Indent>(`/procurement/indents/${encodeURIComponent(id)}`, { signal });
export const updateIndent = (id: string, body: Pick<IndentCreate, "department_id" | "needed_by" | "lines">) =>
  erpRequest<Indent>(`/procurement/indents/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) });
export const submitIndent = (id: string) =>
  erpRequest<Indent>(`/procurement/indents/${encodeURIComponent(id)}/submit`, { method: "POST" });
export const cancelIndent = (id: string) =>
  erpRequest<Indent>(`/procurement/indents/${encodeURIComponent(id)}/cancel`, { method: "POST" });
export const listRfqsPage = (query: { status: string | null; unitId: string | null; limit: number; offset: number }, signal?: AbortSignal) => {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.status) params.set("status", query.status);
  if (query.unitId) params.set("unit_id", query.unitId);
  return erpRequest<Page<RfqSummary>>(`/procurement/rfqs?${params.toString()}`, { signal });
};
export async function fetchAllRfqs(status: string | null, unitId: string | null, signal?: AbortSignal) {
  const rfqs: RfqSummary[] = [];
  let offset = 0, total = Number.POSITIVE_INFINITY;
  while (rfqs.length < total) {
    const page = await listRfqsPage({ status, unitId, limit: 200, offset }, signal);
    total = page.total;
    if (!page.items.length) break;
    rfqs.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return rfqs;
}
export const fetchRfq = (id: string, signal?: AbortSignal) =>
  erpRequest<Rfq>(`/procurement/rfqs/${encodeURIComponent(id)}`, { signal });
export const fetchRfqComparison = (id: string, signal?: AbortSignal) =>
  erpRequest<RfqComparison>(`/procurement/rfqs/${encodeURIComponent(id)}/comparison`, { signal });
export const createRfq = (body: RfqCreate) =>
  erpRequest<Rfq>("/procurement/rfqs", { method: "POST", body: JSON.stringify(body) });
export const updateRfq = (id: string, body: RfqUpdate) =>
  erpRequest<Rfq>(`/procurement/rfqs/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) });
export const inviteRfqVendors = (id: string, vendorIds: string[]) =>
  erpRequest<Rfq>(`/procurement/rfqs/${encodeURIComponent(id)}/vendors`, { method: "POST", body: JSON.stringify({ vendor_ids: vendorIds }) });
export const sendRfq = (id: string) =>
  erpRequest<Rfq>(`/procurement/rfqs/${encodeURIComponent(id)}/send`, { method: "POST" });
export const cancelRfq = (id: string) =>
  erpRequest<Rfq>(`/procurement/rfqs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
export const recordRfqQuote = (id: string, body: RfqQuote) =>
  erpRequest<Rfq>(`/procurement/rfqs/${encodeURIComponent(id)}/quotes`, { method: "POST", body: JSON.stringify(body) });
export const awardRfq = (id: string, body: RfqAward) =>
  erpRequest<{ po_id: string; po_no: string }>(`/procurement/rfqs/${encodeURIComponent(id)}/award`, { method: "POST", body: JSON.stringify(body) });
export const listPosPage = (query: { status: string | null; vendorId: string | null; unitId: string | null; limit: number; offset: number }, signal?: AbortSignal) => {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.status) params.set("status", query.status);
  if (query.vendorId) params.set("vendor_id", query.vendorId);
  if (query.unitId) params.set("unit_id", query.unitId);
  return erpRequest<Page<PoSummary>>(`/procurement/purchase-orders?${params}`, { signal });
};
export async function fetchAllPos(status: string | null, vendorId: string | null, unitId: string | null, signal?: AbortSignal) {
  const pos: PoSummary[] = [];
  let offset = 0, total = Number.POSITIVE_INFINITY;
  while (pos.length < total) {
    const page = await listPosPage({ status, vendorId, unitId, limit: 200, offset }, signal);
    total = page.total;
    if (!page.items.length) break;
    pos.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return pos;
}
export const fetchPo = (id: string, signal?: AbortSignal) =>
  erpRequest<Po>(`/procurement/purchase-orders/${encodeURIComponent(id)}`, { signal });
export const fetchPoHistory = (id: string, signal?: AbortSignal) =>
  erpRequest<PoHistory>(`/procurement/purchase-orders/${encodeURIComponent(id)}/history`, { signal });
export const fetchPoReceiving = (id: string, signal?: AbortSignal) =>
  erpRequest<PoReceivingLine[]>(`/procurement/purchase-orders/${encodeURIComponent(id)}/receiving`, { signal });
export const fetchPoLateLines = (signal?: AbortSignal) =>
  erpRequest<PoLateLine[]>("/procurement/purchase-orders/late-lines", { signal });
export const createPo = (body: PoCreate) =>
  erpRequest<Po>("/procurement/purchase-orders", { method: "POST", body: JSON.stringify(body) });
export const createPoFromIndents = (body: PoFromIndents) =>
  erpRequest<Po>("/procurement/purchase-orders/from-indents", { method: "POST", body: JSON.stringify(body) });
export const updatePo = (id: string, body: PoUpdate) =>
  erpRequest<Po>(`/procurement/purchase-orders/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) });
export const submitPo = (id: string) =>
  erpRequest<Po>(`/procurement/purchase-orders/${encodeURIComponent(id)}/submit`, { method: "POST" });
export const cancelPo = (id: string) =>
  erpRequest<Po>(`/procurement/purchase-orders/${encodeURIComponent(id)}/cancel`, { method: "POST" });
export const closePo = (id: string) =>
  erpRequest<Po>(`/procurement/purchase-orders/${encodeURIComponent(id)}/close`, { method: "POST" });
export const listGrnsPage = (query: { status: string | null; poId: string | null; limit: number; offset: number }, signal?: AbortSignal) => {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.status) params.set("status", query.status);
  if (query.poId) params.set("po_id", query.poId);
  return erpRequest<Page<GrnSummary>>(`/inventory/grns?${params}`, { signal });
};
export async function fetchAllGrns(status: string | null, poId: string | null, signal?: AbortSignal) {
  const grns: GrnSummary[] = [];
  let offset = 0, total = Number.POSITIVE_INFINITY;
  while (grns.length < total) {
    const page = await listGrnsPage({ status, poId, limit: 200, offset }, signal);
    total = page.total;
    if (!page.items.length) break;
    grns.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return grns;
}
export const fetchGrn = (id: string, signal?: AbortSignal) =>
  erpRequest<Grn>(`/inventory/grns/${encodeURIComponent(id)}`, { signal });
export const createGrn = (body: GrnCreate) =>
  erpRequest<Grn>("/inventory/grns", { method: "POST", body: JSON.stringify(body) });
export const inspectGrnLine = (lineId: string, body: { accepted_qty: string; note: string | null }) =>
  erpRequest<Grn>(`/inventory/grn-lines/${encodeURIComponent(lineId)}/inspect`, { method: "POST", body: JSON.stringify(body) });
export const listStockPage = (query: { unitId: string | null; storeId: string | null; itemId: string | null; lotStatus: string | null; inStockOnly: boolean; limit: number; offset: number }, signal?: AbortSignal) => {
  const params = new URLSearchParams({ in_stock_only: String(query.inStockOnly), limit: String(query.limit), offset: String(query.offset) });
  if (query.unitId) params.set("unit_id", query.unitId);
  if (query.storeId) params.set("store_id", query.storeId);
  if (query.itemId) params.set("item_id", query.itemId);
  if (query.lotStatus) params.set("lot_status", query.lotStatus);
  return erpRequest<Page<StockRow>>(`/inventory/stock?${params}`, { signal });
};
export async function fetchAllStock(filters: { unitId: string | null; storeId: string | null; itemId: string | null; lotStatus: string | null; inStockOnly: boolean }, signal?: AbortSignal) {
  const items: StockRow[] = [];
  let offset = 0, total = Number.POSITIVE_INFINITY;
  while (items.length < total) {
    const page = await listStockPage({ ...filters, limit: 200, offset }, signal);
    total = page.total;
    if (!page.items.length) break;
    items.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return items;
}
export const fetchStockLot = (id: string, signal?: AbortSignal) => erpRequest<StockRow>(`/inventory/lots/${encodeURIComponent(id)}`, { signal });
export function listReservations(filters: { status: string; referenceType?: ReservationReferenceType | null; reference?: string | null }, signal?: AbortSignal) {
  const params = new URLSearchParams({ status: filters.status });
  if (filters.referenceType) params.set("reference_type", filters.referenceType);
  if (filters.reference) params.set("reference", filters.reference);
  return erpRequest<Reservation[]>(`/inventory/reservations?${params}`, { signal });
}
export const createReservation = (body: ReservationCreate) => erpRequest<Reservation[]>("/inventory/reservations", { method: "POST", body: JSON.stringify(body) });
export const releaseReservations = (body: ReservationReference) => erpRequest<{ released: number }>("/inventory/reservations/release", { method: "POST", body: JSON.stringify(body) });
export function listTransfersPage(query: { status: string | null; storeId: string | null; limit: number; offset: number }, signal?: AbortSignal) {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.status) params.set("status", query.status);
  if (query.storeId) params.set("store_id", query.storeId);
  return erpRequest<Page<TransferSummary>>(`/inventory/transfers?${params}`, { signal });
}
export async function fetchAllTransfers(status: string | null, storeId: string | null, signal?: AbortSignal) {
  const transfers: TransferSummary[] = [];
  let offset = 0, total = Number.POSITIVE_INFINITY;
  while (transfers.length < total) {
    const page = await listTransfersPage({ status, storeId, limit: 200, offset }, signal);
    total = page.total;
    if (!page.items.length) break;
    transfers.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return transfers;
}
export const fetchTransfer = (id: string, signal?: AbortSignal) => erpRequest<TransferOut>(`/inventory/transfers/${encodeURIComponent(id)}`, { signal });
export const createTransfer = (body: TransferCreate) => erpRequest<TransferOut>("/inventory/transfers", { method: "POST", body: JSON.stringify(body) });
export const transitionTransfer = (id: string, action: "dispatch" | "receive" | "cancel") => erpRequest<TransferOut>(`/inventory/transfers/${encodeURIComponent(id)}/${action}`, { method: "POST" });
export function listCountsPage(query: { status: string | null; storeId: string | null; limit: number; offset: number }, signal?: AbortSignal) {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.status) params.set("status", query.status);
  if (query.storeId) params.set("store_id", query.storeId);
  return erpRequest<Page<CountSummary>>(`/inventory/counts?${params}`, { signal });
}
export async function fetchAllCounts(status: string | null, storeId: string | null, signal?: AbortSignal) {
  const counts: CountSummary[] = [];
  let offset = 0, total = Number.POSITIVE_INFINITY;
  while (counts.length < total) {
    const page = await listCountsPage({ status, storeId, limit: 200, offset }, signal);
    total = page.total;
    if (!page.items.length) break;
    counts.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return counts;
}
export const fetchCount = (id: string, signal?: AbortSignal) => erpRequest<CountOut>(`/inventory/counts/${encodeURIComponent(id)}`, { signal });
export const startCount = (storeId: string) => erpRequest<CountOut>("/inventory/counts", { method: "POST", body: JSON.stringify({ store_id: storeId }) });
export const saveCountLines = (id: string, lines: { count_line_id: string; counted_qty: string }[]) => erpRequest<CountOut>(`/inventory/counts/${encodeURIComponent(id)}/lines`, { method: "PUT", body: JSON.stringify({ lines }) });
export const transitionCount = (id: string, action: "submit" | "cancel" | "recount") => erpRequest<CountOut>(`/inventory/counts/${encodeURIComponent(id)}/${action}`, { method: "POST" });
export function listCompoundsPage(query: { active: string | null; lane: string | null; limit: number; offset: number }, signal?: AbortSignal) {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.active !== null) params.set("active", query.active);
  if (query.lane) params.set("lane", query.lane);
  return erpRequest<Page<Compound>>(`/manufacturing/compounds?${params}`, { signal });
}
export async function fetchAllCompounds(active: string | null, lane: string | null, signal?: AbortSignal) {
  const compounds: Compound[] = [];
  let offset = 0, total = Number.POSITIVE_INFINITY;
  while (compounds.length < total) {
    const page = await listCompoundsPage({ active, lane, limit: 200, offset }, signal);
    total = page.total;
    if (!page.items.length) break;
    compounds.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return compounds;
}
export const fetchCompound = (id: string, signal?: AbortSignal) => erpRequest<Compound>(`/manufacturing/compounds/${encodeURIComponent(id)}`, { signal });
export const createCompound = (body: CompoundCreate) => erpRequest<Compound>("/manufacturing/compounds", { method: "POST", body: JSON.stringify(body) });
export const updateCompound = (id: string, body: CompoundPatch) => erpRequest<Compound>(`/manufacturing/compounds/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) });
export function fetchCompoundAvailability(id: string, unitId: string | null, signal?: AbortSignal) {
  const params = new URLSearchParams();
  if (unitId) params.set("unit_id", unitId);
  return erpRequest<CompoundAvailability>(`/manufacturing/compounds/${encodeURIComponent(id)}/available${params.size ? `?${params}` : ""}`, { signal });
}
export function listCompoundBatchesPage(query: { status: string | null; compoundId: string | null; limit: number; offset: number }, signal?: AbortSignal) {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.status) params.set("status", query.status);
  if (query.compoundId) params.set("compound_id", query.compoundId);
  return erpRequest<Page<CompoundBatchSummary>>(`/manufacturing/batches?${params}`, { signal });
}
export async function fetchAllCompoundBatches(status: string | null, compoundId: string | null, signal?: AbortSignal) {
  const batches: CompoundBatchSummary[] = [];
  let offset = 0, total = Number.POSITIVE_INFINITY;
  while (batches.length < total) {
    const page = await listCompoundBatchesPage({ status, compoundId, limit: 200, offset }, signal);
    total = page.total;
    if (!page.items.length) break;
    batches.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return batches;
}
export const fetchCompoundBatch = (id: string, signal?: AbortSignal) => erpRequest<CompoundBatch>(`/manufacturing/batches/${encodeURIComponent(id)}`, { signal });
export const fetchBatchUsedIn = (id: string, signal?: AbortSignal) => erpRequest<ProductionLotSummary[]>(`/manufacturing/batches/${encodeURIComponent(id)}/used-in`, { signal });
export const createCompoundBatch = (body: CompoundBatchCreate) => erpRequest<CompoundBatch>("/manufacturing/batches", { method: "POST", body: JSON.stringify(body) });
export function transitionBatch(id: string, action: "start-mixing" | "mixed" | "qa", body?: { result: "PASSED" | "FAILED"; hardness?: string | null; note?: string | null }) {
  return erpRequest<CompoundBatch>(`/manufacturing/batches/${encodeURIComponent(id)}/${action}`, { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) });
}
type EquipmentQuery = { unitId: string | null; active: string | null; limit: number; offset: number };
function equipmentParams(query: EquipmentQuery) {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.unitId) params.set("unit_id", query.unitId);
  if (query.active !== null) params.set("active", query.active);
  return params;
}
export const listMachinesPage = (query: EquipmentQuery, signal?: AbortSignal) => erpRequest<Page<Machine>>(`/manufacturing/machines?${equipmentParams(query)}`, { signal });
export const listMouldsPage = (query: EquipmentQuery, signal?: AbortSignal) => erpRequest<Page<Mould>>(`/manufacturing/moulds?${equipmentParams(query)}`, { signal });
export async function fetchAllMachines(unitId: string | null, active: string | null, signal?: AbortSignal) {
  const items: Machine[] = [];
  let offset = 0, total = Number.POSITIVE_INFINITY;
  while (items.length < total) {
    const page = await listMachinesPage({ unitId, active, limit: 200, offset }, signal);
    total = page.total;
    if (!page.items.length) break;
    items.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return items;
}
export async function fetchAllMoulds(unitId: string | null, active: string | null, signal?: AbortSignal) {
  const items: Mould[] = [];
  let offset = 0, total = Number.POSITIVE_INFINITY;
  while (items.length < total) {
    const page = await listMouldsPage({ unitId, active, limit: 200, offset }, signal);
    total = page.total;
    if (!page.items.length) break;
    items.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return items;
}
export const createMachine = (body: MachineCreate) => erpRequest<Machine>("/manufacturing/machines", { method: "POST", body: JSON.stringify(body) });
export const updateMachine = (id: string, body: MachinePatch) => erpRequest<Machine>(`/manufacturing/machines/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) });
export const createMould = (body: MouldCreate) => erpRequest<Mould>("/manufacturing/moulds", { method: "POST", body: JSON.stringify(body) });
export const updateMould = (id: string, body: MouldPatch) => erpRequest<Mould>(`/manufacturing/moulds/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) });
export function listWorkOrdersPage(query: { status: string | null; unitId: string | null; itemId: string | null; limit: number; offset: number }, signal?: AbortSignal) {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.status) params.set("status", query.status);
  if (query.unitId) params.set("unit_id", query.unitId);
  if (query.itemId) params.set("item_id", query.itemId);
  return erpRequest<Page<WorkOrderSummary>>(`/manufacturing/work-orders?${params}`, { signal });
}
export const createWorkOrder = (body: WorkOrderCreate) => erpRequest<WorkOrder>("/manufacturing/work-orders", { method: "POST", body: JSON.stringify(body) });
export async function fetchAllWorkOrders(signal?: AbortSignal) {
  const items: WorkOrderSummary[] = [];
  let offset = 0, total = Number.POSITIVE_INFINITY;
  while (items.length < total) {
    const page = await listWorkOrdersPage({ status: null, unitId: null, itemId: null, limit: 200, offset }, signal);
    total = page.total;
    if (!page.items.length) break;
    items.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return items;
}
export function listProductionLotsPage(query: { workOrderId: string | null; status: string | null; stage: string | null; limit: number; offset: number }, signal?: AbortSignal) {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.workOrderId) params.set("work_order_id", query.workOrderId);
  if (query.status) params.set("status", query.status);
  if (query.stage) params.set("stage", query.stage);
  return erpRequest<Page<ProductionLotSummary>>(`/manufacturing/lots?${params}`, { signal });
}
export async function fetchAllProductionLots(workOrderId: string | null, status: string | null, stage: string | null, signal?: AbortSignal) {
  const items: ProductionLotSummary[] = [];
  let offset = 0, total = Number.POSITIVE_INFINITY;
  while (items.length < total) {
    const page = await listProductionLotsPage({ workOrderId, status, stage, limit: 200, offset }, signal);
    total = page.total;
    if (!page.items.length) break;
    items.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return items;
}
export const createProductionLot = (body: ProductionLotCreate) =>
  erpRequest<ProductionLot>("/manufacturing/lots", { method: "POST", body: JSON.stringify(body) });
export const fetchProductionLot = (id: string, signal?: AbortSignal) => erpRequest<ProductionLot>(`/manufacturing/lots/${encodeURIComponent(id)}`, { signal });
export const fetchProductionLotTrace = (id: string, signal?: AbortSignal) => erpRequest<ProductionLotTrace>(`/manufacturing/lots/${encodeURIComponent(id)}/trace`, { signal });
export const moveProductionLot = (id: string, body: { rejects: string; notes: string | null }) => erpRequest<ProductionLot>(`/manufacturing/lots/${encodeURIComponent(id)}/move`, { method: "POST", body: JSON.stringify(body) });
export const packProductionLot = (id: string, body: { fg_store_id: string; notes: string | null }) => erpRequest<ProductionLot>(`/manufacturing/lots/${encodeURIComponent(id)}/pack`, { method: "POST", body: JSON.stringify(body) });
export const reworkProductionLot = (id: string, body: { quantity: string; reason: string }) => erpRequest<ProductionLot>(`/manufacturing/lots/${encodeURIComponent(id)}/rework`, { method: "POST", body: JSON.stringify(body) });
export const returnProductionLotMaterial = (id: string, body: { source_lot_no: string; quantity: string }) => erpRequest<ProductionLot>(`/manufacturing/lots/${encodeURIComponent(id)}/return-material`, { method: "POST", body: JSON.stringify(body) });
export function listShiftEntriesPage(query: { workOrderId: string | null; machineId: string | null; dateFrom: string | null; dateTo: string | null; limit: number; offset: number }, signal?: AbortSignal) {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.workOrderId) params.set("work_order_id", query.workOrderId);
  if (query.machineId) params.set("machine_id", query.machineId);
  if (query.dateFrom) params.set("date_from", query.dateFrom);
  if (query.dateTo) params.set("date_to", query.dateTo);
  return erpRequest<Page<ShiftEntry>>(`/manufacturing/shift-entries?${params}`, { signal });
}
export async function fetchAllShiftEntries(query: { workOrderId: string | null; machineId: string | null; dateFrom: string | null; dateTo: string | null }, signal?: AbortSignal) {
  const items: ShiftEntry[] = [];
  let offset = 0, total = Number.POSITIVE_INFINITY;
  while (items.length < total) {
    const page = await listShiftEntriesPage({ ...query, limit: 200, offset }, signal);
    total = page.total;
    if (!page.items.length) break;
    items.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return items;
}
export const createShiftEntry = (body: ShiftEntryCreate) => erpRequest<ShiftEntry>("/manufacturing/shift-entries", { method: "POST", body: JSON.stringify(body) });
export function fetchMissingShiftEntries(workDate: string, shiftCode: "A" | "B" | "C" | null, signal?: AbortSignal) {
  const params = new URLSearchParams({ work_date: workDate });
  if (shiftCode) params.set("shift_code", shiftCode);
  return erpRequest<MissingShiftEntry[]>(`/manufacturing/shift-entries/missing?${params}`, { signal });
}
export function fetchManufacturingWip(unitId: string | null, signal?: AbortSignal) {
  const params = new URLSearchParams();
  if (unitId) params.set("unit_id", unitId);
  return erpRequest<ManufacturingWip[]>(`/manufacturing/wip${params.size ? `?${params}` : ""}`, { signal });
}
export function fetchManufacturingOee(dateFrom: string, dateTo: string, unitId: string | null, signal?: AbortSignal) {
  const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
  if (unitId) params.set("unit_id", unitId);
  return erpRequest<ManufacturingOee[]>(`/manufacturing/oee?${params}`, { signal });
}
export function fetchQualityQueue(subjectType: QualityQueueKind | null, signal?: AbortSignal) {
  const params = new URLSearchParams();
  if (subjectType) params.set("subject_type", subjectType);
  return erpRequest<QualityQueueRow[]>(`/quality/queue${params.size ? `?${params}` : ""}`, { signal });
}
export function listInspectionPlansPage(query: { itemId: string | null; kind: InspectionPlanKind | null; active: boolean | null; limit: number; offset: number }, signal?: AbortSignal) {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.itemId) params.set("item_id", query.itemId);
  if (query.kind) params.set("kind", query.kind);
  if (query.active !== null) params.set("active", String(query.active));
  return erpRequest<Page<InspectionPlan>>(`/quality/plans?${params}`, { signal });
}
const inspectionPlanPath = (id: string) => `/quality/plans/${encodeURIComponent(id)}`;
export const fetchInspectionPlan = (id: string, signal?: AbortSignal) => erpRequest<InspectionPlan>(inspectionPlanPath(id), { signal });
export const createInspectionPlan = (body: InspectionPlanCreate) => erpRequest<InspectionPlan>("/quality/plans", { method: "POST", body: JSON.stringify(body) });
export const retireInspectionPlan = (id: string) => erpRequest<InspectionPlan>(`${inspectionPlanPath(id)}/retire`, { method: "POST" });
export function listInspectionsPage(query: { subjectType: InspectionSubjectType | null; subjectId: string | null; kind: InspectionPlanKind | null; result: InspectionResult | null; limit: number; offset: number }, signal?: AbortSignal) {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.subjectType) params.set("subject_type", query.subjectType);
  if (query.subjectId) params.set("subject_id", query.subjectId);
  if (query.kind) params.set("kind", query.kind);
  if (query.result) params.set("result", query.result);
  return erpRequest<Page<InspectionSummary>>(`/quality/inspections?${params}`, { signal });
}
const inspectionPath = (id: string) => `/quality/inspections/${encodeURIComponent(id)}`;
export const fetchInspection = (id: string, signal?: AbortSignal) => erpRequest<Inspection>(inspectionPath(id), { signal });
export const createInspection = (body: InspectionCreate) => erpRequest<Inspection>("/quality/inspections", { method: "POST", body: JSON.stringify(body) });
export function listQualityInstrumentsPage(query: { unitId: string | null; calibrationStatus: string | null; active: boolean | null; limit: number; offset: number }, signal?: AbortSignal) {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.unitId) params.set("unit_id", query.unitId);
  if (query.calibrationStatus) params.set("calibration_status", query.calibrationStatus);
  if (query.active !== null) params.set("active", String(query.active));
  return erpRequest<Page<QualityInstrument>>(`/quality/instruments?${params}`, { signal });
}
export function listQualityHoldsPage(query: { active: boolean | null; subjectType: QualityHoldType | null; subjectId: string | null; limit: number; offset: number }, signal?: AbortSignal) {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.active !== null) params.set("active", String(query.active));
  if (query.subjectType) params.set("subject_type", query.subjectType);
  if (query.subjectId) params.set("subject_id", query.subjectId);
  return erpRequest<Page<QualityHold>>(`/quality/holds?${params}`, { signal });
}
export async function fetchAllQualityHolds(query: { active: boolean | null; subjectType: QualityHoldType | null; subjectId: string | null }, signal?: AbortSignal) {
  const rows: QualityHold[] = []; let offset = 0, total = Number.POSITIVE_INFINITY;
  while (rows.length < total) { const page = await listQualityHoldsPage({ ...query, limit: 50, offset }, signal); total = page.total; if (!page.items.length) break; rows.push(...page.items); offset = page.offset + page.items.length; }
  return rows;
}
const qualityHoldPath = (id: string) => `/quality/holds/${encodeURIComponent(id)}`;
export const fetchQualityHold = (id: string, signal?: AbortSignal) => erpRequest<QualityHold>(qualityHoldPath(id), { signal });
export const createQualityHold = (body: { subject_type: QualityHoldType; subject_id: string; reason: string }) => erpRequest<QualityHold>("/quality/holds", { method: "POST", body: JSON.stringify(body) });
export const releaseQualityHold = (id: string, note: string) => erpRequest<QualityHold>(`${qualityHoldPath(id)}/release`, { method: "POST", body: JSON.stringify({ note }) });
export const fetchWorkOrder = (id: string, signal?: AbortSignal) => erpRequest<WorkOrder>(`/manufacturing/work-orders/${encodeURIComponent(id)}`, { signal });
export const fetchWorkOrderVariance = (id: string, signal?: AbortSignal) => erpRequest<WorkOrderMaterialVariance[]>(`/manufacturing/work-orders/${encodeURIComponent(id)}/material-variance`, { signal });
export const transitionWorkOrder = (id: string, action: "release" | "cancel" | "complete") => erpRequest<WorkOrder>(`/manufacturing/work-orders/${encodeURIComponent(id)}/${action}`, { method: "POST" });
export async function fetchAllWorkOrderLots(id: string, signal?: AbortSignal) {
  const items: ProductionLotSummary[] = [];
  const seen = new Set<string>();
  let offset = 0, total = Number.POSITIVE_INFINITY;
  while (items.length < total) {
    const params = new URLSearchParams({ work_order_id: id, limit: "200", offset: String(offset) });
    const page = await erpRequest<Page<ProductionLotSummary>>(`/manufacturing/lots?${params}`, { signal });
    total = page.total;
    if (page.offset !== offset || page.items.some(lot => seen.has(lot.prod_lot_id)) || new Set(page.items.map(lot => lot.prod_lot_id)).size !== page.items.length) throw new Error("Lot results changed while loading. Refresh before taking an action.");
    if (!page.items.length) {
      if (items.length < total) throw new Error("Lot results were incomplete. Refresh before taking an action.");
      break;
    }
    page.items.forEach(lot => seen.add(lot.prod_lot_id));
    items.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return items;
}
export function fetchInventoryValuation(unitId: string | null, signal?: AbortSignal) {
  const params = new URLSearchParams();
  if (unitId) params.set("unit_id", unitId);
  return erpRequest<InventoryValuation[]>(`/inventory/valuation${params.size ? `?${params}` : ""}`, { signal });
}
export function fetchInventoryExpiring(withinDays: number, includeExpired: boolean, signal?: AbortSignal) {
  const params = new URLSearchParams({ within_days: String(withinDays), include_expired: String(includeExpired) });
  return erpRequest<InventoryExpiring[]>(`/inventory/expiring?${params}`, { signal });
}
export function fetchInventoryReorder(unitId: string | null, signal?: AbortSignal) {
  const params = new URLSearchParams();
  if (unitId) params.set("unit_id", unitId);
  return erpRequest<InventoryReorder[]>(`/inventory/reorder${params.size ? `?${params}` : ""}`, { signal });
}
export const fetchInventoryReconciliation = (signal?: AbortSignal) => erpRequest<InventoryReconciliation>("/inventory/reconciliation", { signal });
export const fetchLotTrace = (id: string, signal?: AbortSignal) => erpRequest<LotTrace>(`/inventory/lots/${encodeURIComponent(id)}/trace`, { signal });
export const quarantineLot = (id: string, reason: string) => erpRequest<StockRow>(`/inventory/lots/${encodeURIComponent(id)}/quarantine`, { method: "POST", body: JSON.stringify({ reason }) });
export const unquarantineLot = (id: string, reason: string) => erpRequest<StockRow>(`/inventory/lots/${encodeURIComponent(id)}/unquarantine`, { method: "POST", body: JSON.stringify({ reason }) });
export const planStockIssue = (body: IssueInput, signal?: AbortSignal) => erpRequest<IssuePlan>("/inventory/issues/plan", { method: "POST", body: JSON.stringify(body), signal });
export const postStockIssue = (body: IssueInput & { purpose: string; reference: string | null; use_reservation: null }) => erpRequest<IssueOut>("/inventory/issues", { method: "POST", body: JSON.stringify(body) });
export const listIssuesPage = (storeId: string | null, limit: number, offset: number, signal?: AbortSignal) => {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (storeId) params.set("store_id", storeId);
  return erpRequest<Page<IssueSummary>>(`/inventory/issues?${params}`, { signal });
};
export async function fetchAllIssues(storeId: string | null, signal?: AbortSignal) {
  const issues: IssueSummary[] = [];
  let offset = 0, total = Number.POSITIVE_INFINITY;
  while (issues.length < total) {
    const page = await listIssuesPage(storeId, 200, offset, signal);
    total = page.total;
    if (!page.items.length) break;
    issues.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return issues;
}
export const fetchIssue = (id: string, signal?: AbortSignal) => erpRequest<IssueOut>(`/inventory/issues/${encodeURIComponent(id)}`, { signal });

export async function fetchAllOrganizationUnits(signal?: AbortSignal): Promise<Unit[]> {
  const units: Unit[] = [];
  let offset = 0;
  let total = Number.POSITIVE_INFINITY;
  while (units.length < total) {
    const page = await listUnits(offset, 200, signal);
    total = page.total;
    if (page.items.length === 0) break;
    units.push(...page.items);
    offset = page.offset + page.items.length;
  }
  return Array.from(new Map(units.map(unit => [unit.unit_id, unit])).values());
}

export async function fetchAllUnits(): Promise<Unit[]> {
  const units: Unit[] = [];
  let offset = 0;
  const limit = 200;
  let total = Number.POSITIVE_INFINITY;

  while (units.length < total) {
    const page = await fetchUnitsPage(offset, limit);
    total = page.total;
    if (page.items.length === 0) break;
    units.push(...page.items);
    offset = page.offset + page.items.length;
  }

  return Array.from(new Map(units.map(unit => [unit.unit_id, unit])).values());
}

export type NcrSourceType = "GRN_LINE" | "STOCK_LOT" | "COMPOUND_BATCH" | "PROD_LOT" | "SUPPLIER" | "CUSTOMER_COMPLAINT";
export type NcrDisposition = "USE_AS_IS" | "REWORK" | "SORT" | "SCRAP" | "RETURN_TO_SUPPLIER";
export type NcrStepCode = "D1" | "D2" | "D3" | "D4" | "D5" | "D6" | "D7" | "D8";
export type NcrSummary = {
  ncr_id: string; ncr_no: string; source_type: NcrSourceType; source_id: string; title: string;
  severity: string; status: string; owner_name: string | null; opened_at: string;
  disposition: NcrDisposition | null; closed_at: string | null; unit_id: string | null;
};
export type NcrStep = {
  step_code: NcrStepCode; title: string; owner_name: string | null; due_at: string | null;
  completed_at: string | null; completed_by_name: string | null; action_text: string | null; evidence_ref: string | null;
};
export type Ncr = NcrSummary & {
  description: string | null; source_label: string | null; disposition_note: string | null;
  disposition_by_name: string | null; steps: NcrStep[]; open_hold_id: string | null;
};
export type NcrCreate = {
  source_type: NcrSourceType; source_id: string; title: string; description: string | null;
  severity: "MINOR" | "MAJOR" | "CRITICAL"; owner_user_id?: string; hold: boolean;
};
export type NcrStepUpdate = { action_text?: string; owner_user_id?: string; due_at?: string; complete: boolean };
export type NcrFilters = { status?: string | null; sourceType?: NcrSourceType | null; severity?: string | null };
export function listNcrsPage(filters: NcrFilters, limit: number, offset: number, signal?: AbortSignal) {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (filters.status) query.set("status", filters.status);
  if (filters.sourceType) query.set("source_type", filters.sourceType);
  if (filters.severity) query.set("severity", filters.severity);
  return erpRequest<Page<NcrSummary>>(`/quality/ncrs?${query}`, { signal });
}
export async function fetchAllNcrs(filters: NcrFilters, signal?: AbortSignal) {
  const rows: NcrSummary[] = [], seen = new Set<string>(); let offset = 0, expectedTotal: number | undefined;
  while (expectedTotal === undefined || rows.length < expectedTotal) {
    const page = await listNcrsPage(filters, 50, offset, signal);
    if (page.offset !== offset || (expectedTotal !== undefined && expectedTotal !== page.total)
      || (page.items.length === 0 && rows.length < page.total)
      || new Set(page.items.map(row => row.ncr_id)).size !== page.items.length
      || page.items.some(row => seen.has(row.ncr_id))) throw new Error("NCR results changed or were incomplete. Refresh the register before filtering.");
    expectedTotal = page.total;
    page.items.forEach(row => seen.add(row.ncr_id)); rows.push(...page.items); offset += page.items.length;
  }
  return rows;
}
const ncrPath = (id: string) => `/quality/ncrs/${encodeURIComponent(id)}`;
export const fetchNcr = (id: string, signal?: AbortSignal) => erpRequest<Ncr>(ncrPath(id), { signal });
export const createNcr = (body: NcrCreate) => erpRequest<Ncr>("/quality/ncrs", { method: "POST", body: JSON.stringify(body) });
export const updateNcrStep = (id: string, code: NcrStepCode, body: NcrStepUpdate) => erpRequest<Ncr>(`${ncrPath(id)}/steps/${code}`, { method: "PATCH", body: JSON.stringify(body) });
export const recordNcrDisposition = (id: string, disposition: NcrDisposition, note: string) => erpRequest<Ncr>(`${ncrPath(id)}/disposition`, { method: "POST", body: JSON.stringify({ disposition, note }) });
export const transitionNcr = (id: string, action: "close" | "cancel") => erpRequest<Ncr>(`${ncrPath(id)}/${action}`, { method: "POST" });
