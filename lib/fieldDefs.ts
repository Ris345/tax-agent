/**
 * Field definitions for the tax document review UI.
 *
 * Each FieldDef describes how a single extracted field should be rendered:
 *   - label / type / required / sensitive for display
 *   - placeholder for empty-state hint text
 *
 * FIELD_SECTIONS maps document_type → ordered array of SectionDef.
 * Components iterate sections then fields within each section.
 */

export type FieldType =
  | 'text'      // free-form string
  | 'number'    // integer
  | 'currency'  // decimal dollar amount
  | 'boolean'   // Yes / No select
  | 'ssn'       // Social Security Number — masked by default
  | 'ein'       // Employer ID Number — masked by default
  | 'year';     // 4-digit tax year

export interface FieldDef {
  key:          string;
  label:        string;
  type:         FieldType;
  required?:    boolean;
  sensitive?:   boolean;  // if true, value is masked until user reveals it
  placeholder?: string;
}

export interface SectionDef {
  id:     string;
  title:  string;
  fields: FieldDef[];
}

// ── W-2 ───────────────────────────────────────────────────────────────────────

const W2_SECTIONS: SectionDef[] = [
  {
    id: 'employer',
    title: 'Employer Information',
    fields: [
      { key: 'employer_name',    label: 'Employer Name',                 type: 'text',     required: true,  sensitive: false },
      { key: 'employer_ein',     label: 'Employer ID Number (EIN)',       type: 'ein',      required: true,  sensitive: true  },
      { key: 'employer_address', label: 'Employer Address',               type: 'text',     required: false, sensitive: false },
    ],
  },
  {
    id: 'employee',
    title: 'Employee Information',
    fields: [
      { key: 'employee_first_name', label: 'First Name',                  type: 'text',     required: true,  sensitive: false },
      { key: 'employee_last_name',  label: 'Last Name',                   type: 'text',     required: true,  sensitive: false },
      { key: 'employee_ssn',        label: 'Social Security Number (SSN)',type: 'ssn',      required: true,  sensitive: true  },
      { key: 'employee_address',    label: 'Employee Address',            type: 'text',     required: false, sensitive: false },
    ],
  },
  {
    id: 'tax_year',
    title: 'Tax Year',
    fields: [
      { key: 'tax_year', label: 'Tax Year', type: 'year', required: true, sensitive: false },
    ],
  },
  {
    id: 'wages_taxes',
    title: 'Wages & Taxes',
    fields: [
      { key: 'wages_tips_other_compensation', label: 'Box 1 — Wages, Tips, Other Compensation', type: 'currency', required: true,  sensitive: false },
      { key: 'federal_income_tax_withheld',   label: 'Box 2 — Federal Income Tax Withheld',     type: 'currency', required: true,  sensitive: false },
      { key: 'social_security_wages',         label: 'Box 3 — Social Security Wages',           type: 'currency', required: false, sensitive: false },
      { key: 'social_security_tax_withheld',  label: 'Box 4 — Social Security Tax Withheld',    type: 'currency', required: false, sensitive: false },
      { key: 'medicare_wages_tips',           label: 'Box 5 — Medicare Wages and Tips',         type: 'currency', required: false, sensitive: false },
      { key: 'medicare_tax_withheld',         label: 'Box 6 — Medicare Tax Withheld',           type: 'currency', required: false, sensitive: false },
    ],
  },
  {
    id: 'additional',
    title: 'Additional Compensation',
    fields: [
      { key: 'social_security_tips',   label: 'Box 7 — Social Security Tips',    type: 'currency', required: false, sensitive: false },
      { key: 'allocated_tips',         label: 'Box 8 — Allocated Tips',          type: 'currency', required: false, sensitive: false },
      { key: 'dependent_care_benefits',label: 'Box 10 — Dependent Care Benefits',type: 'currency', required: false, sensitive: false },
      { key: 'nonqualified_plans',     label: 'Box 11 — Nonqualified Plans',     type: 'currency', required: false, sensitive: false },
    ],
  },
  {
    id: 'box12',
    title: 'Box 12 — Deferred Compensation',
    fields: [
      { key: 'box_12a', label: 'Box 12a', type: 'text', required: false, sensitive: false, placeholder: 'e.g. D 5000.00' },
      { key: 'box_12b', label: 'Box 12b', type: 'text', required: false, sensitive: false, placeholder: 'e.g. DD 3500.00' },
      { key: 'box_12c', label: 'Box 12c', type: 'text', required: false, sensitive: false },
      { key: 'box_12d', label: 'Box 12d', type: 'text', required: false, sensitive: false },
    ],
  },
  {
    id: 'box13',
    title: 'Box 13 — Checkboxes',
    fields: [
      { key: 'statutory_employee',    label: 'Statutory Employee',     type: 'boolean', required: false, sensitive: false },
      { key: 'retirement_plan',       label: 'Retirement Plan',        type: 'boolean', required: false, sensitive: false },
      { key: 'third_party_sick_pay',  label: 'Third-Party Sick Pay',   type: 'boolean', required: false, sensitive: false },
    ],
  },
  {
    id: 'state',
    title: 'State Tax Information',
    fields: [
      { key: 'state',            label: 'State (Box 15)',                   type: 'text',     required: false, sensitive: false, placeholder: 'e.g. CA' },
      { key: 'employer_state_id',label: "Employer's State ID (Box 15)",     type: 'text',     required: false, sensitive: false },
      { key: 'state_wages_tips', label: 'State Wages, Tips (Box 16)',       type: 'currency', required: false, sensitive: false },
      { key: 'state_income_tax', label: 'State Income Tax (Box 17)',        type: 'currency', required: false, sensitive: false },
    ],
  },
];

// ── 1099-NEC ──────────────────────────────────────────────────────────────────

const NEC_SECTIONS: SectionDef[] = [
  {
    id: 'payer',
    title: 'Payer Information',
    fields: [
      { key: 'payer_name',    label: "Payer's Name",          type: 'text', required: true,  sensitive: false },
      { key: 'payer_tin',     label: "Payer's TIN",           type: 'ein',  required: true,  sensitive: true  },
      { key: 'payer_address', label: "Payer's Address",       type: 'text', required: false, sensitive: false },
    ],
  },
  {
    id: 'recipient',
    title: 'Recipient Information',
    fields: [
      { key: 'recipient_name',    label: "Recipient's Name",    type: 'text', required: true,  sensitive: false },
      { key: 'recipient_tin',     label: "Recipient's TIN/SSN", type: 'ssn',  required: true,  sensitive: true  },
      { key: 'recipient_address', label: "Recipient's Address", type: 'text', required: false, sensitive: false },
      { key: 'account_number',    label: 'Account Number',      type: 'text', required: false, sensitive: true  },
    ],
  },
  {
    id: 'tax_year',
    title: 'Tax Year',
    fields: [
      { key: 'tax_year', label: 'Tax Year', type: 'year', required: true, sensitive: false },
    ],
  },
  {
    id: 'income',
    title: 'Income',
    fields: [
      { key: 'nonemployee_compensation', label: 'Box 1 — Nonemployee Compensation', type: 'currency', required: true,  sensitive: false },
      { key: 'direct_sales_indicator',   label: 'Box 2 — Direct Sales ≥ $5,000',   type: 'boolean',  required: false, sensitive: false },
      { key: 'federal_tax_withheld',     label: 'Box 4 — Federal Tax Withheld',    type: 'currency', required: false, sensitive: false },
      { key: 'state_tax_withheld',       label: 'Box 5 — State Tax Withheld',      type: 'currency', required: false, sensitive: false },
      { key: 'state',                    label: 'State',                           type: 'text',     required: false, sensitive: false, placeholder: 'e.g. CA' },
      { key: 'payer_state_no',           label: "Payer's State No.",               type: 'text',     required: false, sensitive: false },
      { key: 'state_income',             label: 'State Income',                    type: 'currency', required: false, sensitive: false },
    ],
  },
];

// ── 1099-B ────────────────────────────────────────────────────────────────────

const B_SECTIONS: SectionDef[] = [
  {
    id: 'payer',
    title: 'Payer Information',
    fields: [
      { key: 'payer_name',    label: "Payer's Name",    type: 'text', required: true,  sensitive: false },
      { key: 'payer_tin',     label: "Payer's TIN",     type: 'ein',  required: true,  sensitive: true  },
      { key: 'payer_address', label: "Payer's Address", type: 'text', required: false, sensitive: false },
    ],
  },
  {
    id: 'recipient',
    title: 'Recipient Information',
    fields: [
      { key: 'recipient_name',    label: "Recipient's Name",    type: 'text', required: true,  sensitive: false },
      { key: 'recipient_tin',     label: "Recipient's TIN/SSN", type: 'ssn',  required: true,  sensitive: true  },
      { key: 'recipient_address', label: "Recipient's Address", type: 'text', required: false, sensitive: false },
      { key: 'account_number',    label: 'Account Number',      type: 'text', required: false, sensitive: true  },
    ],
  },
  {
    id: 'tax_year',
    title: 'Tax Year',
    fields: [
      { key: 'tax_year', label: 'Tax Year', type: 'year', required: true, sensitive: false },
    ],
  },
  {
    id: 'summary',
    title: 'Summary Totals',
    fields: [
      { key: 'total_proceeds',        label: 'Total Proceeds (Box 1d)',       type: 'currency', required: false, sensitive: false },
      { key: 'total_cost_basis',      label: 'Total Cost Basis (Box 1e)',     type: 'currency', required: false, sensitive: false },
      { key: 'total_gain_loss',       label: 'Total Gain / Loss',             type: 'currency', required: false, sensitive: false },
      { key: 'federal_tax_withheld',  label: 'Federal Tax Withheld (Box 4)', type: 'currency', required: false, sensitive: false },
    ],
  },
];

// ── 1099-INT ──────────────────────────────────────────────────────────────────

const INT_SECTIONS: SectionDef[] = [
  {
    id: 'payer',
    title: 'Payer Information',
    fields: [
      { key: 'payer_name',    label: "Payer's Name",    type: 'text', required: true,  sensitive: false },
      { key: 'payer_tin',     label: "Payer's TIN",     type: 'ein',  required: true,  sensitive: true  },
      { key: 'payer_address', label: "Payer's Address", type: 'text', required: false, sensitive: false },
    ],
  },
  {
    id: 'recipient',
    title: 'Recipient Information',
    fields: [
      { key: 'recipient_name',    label: "Recipient's Name",    type: 'text', required: true,  sensitive: false },
      { key: 'recipient_tin',     label: "Recipient's TIN/SSN", type: 'ssn',  required: true,  sensitive: true  },
      { key: 'recipient_address', label: "Recipient's Address", type: 'text', required: false, sensitive: false },
      { key: 'account_number',    label: 'Account Number',      type: 'text', required: false, sensitive: true  },
    ],
  },
  {
    id: 'tax_year',
    title: 'Tax Year',
    fields: [
      { key: 'tax_year', label: 'Tax Year', type: 'year', required: true, sensitive: false },
    ],
  },
  {
    id: 'interest',
    title: 'Interest Income',
    fields: [
      { key: 'interest_income',            label: 'Box 1 — Interest Income',               type: 'currency', required: true,  sensitive: false },
      { key: 'early_withdrawal_penalty',   label: 'Box 2 — Early Withdrawal Penalty',      type: 'currency', required: false, sensitive: false },
      { key: 'us_savings_bond_interest',   label: 'Box 3 — US Savings Bond Interest',      type: 'currency', required: false, sensitive: false },
      { key: 'federal_tax_withheld',       label: 'Box 4 — Federal Tax Withheld',          type: 'currency', required: false, sensitive: false },
      { key: 'investment_expenses',        label: 'Box 5 — Investment Expenses',           type: 'currency', required: false, sensitive: false },
      { key: 'foreign_tax_paid',           label: 'Box 6 — Foreign Tax Paid',              type: 'currency', required: false, sensitive: false },
      { key: 'tax_exempt_interest',        label: 'Box 8 — Tax-Exempt Interest',           type: 'currency', required: false, sensitive: false },
      { key: 'state_tax_withheld',         label: 'Box 17 — State Tax Withheld',           type: 'currency', required: false, sensitive: false },
    ],
  },
];

// ── Registry ──────────────────────────────────────────────────────────────────

export const FIELD_SECTIONS: Record<string, SectionDef[]> = {
  'W2':       W2_SECTIONS,
  '1099-NEC': NEC_SECTIONS,
  '1099-B':   B_SECTIONS,
  '1099-INT': INT_SECTIONS,
};
