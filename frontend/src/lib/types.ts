// Shapes mirroring the agent's Redis data + LangGraph debate state.

export interface CompanyFinancials {
  name: string;
  stage: string;
  sector?: string;
  hq?: string;
  headcount: number;
  cash_on_hand: number;
  monthly_revenue: number;
  monthly_gross_burn: number;
  monthly_net_burn: number;
  runway_months: number;
  mrr: number;
  arr: number;
  mrr_growth_mom: number;
  gross_margin: number;
  logo_churn_mom: number;
  ndr: number;
  cac: number;
  ltv: number;
  opex_monthly: { rd: number; sm: number; ga: number };
  last_raise?: {
    round: string;
    amount: number;
    date: string;
    lead: string;
    post_money: number;
  };
  cash_history: { month: string; cash: number; net_burn: number }[];
}

export interface Vendor {
  name: string;
  category: string;
  annual_cost: number;
  monthly_cost: number;
  renewal_date: string;
  status: string;
  notes?: string;
}

export interface DecisionEvent {
  _id: string;
  title: string;
  summary?: string;
  decision?: string;
  confidence?: number;
  source?: string;
}

export type TurnType = "framing" | "position" | "rebuttal" | "decision";
export type Stance = "support" | "oppose" | "conditional";

export interface TranscriptTurn {
  agent?: string;
  label?: string;
  role?: string;
  monogram?: string;
  type: TurnType;
  stance?: Stance;
  headline?: string;
  argument?: string;
  key_points?: string[];
  // rebuttal-only
  from_role?: string;
  to_role?: string;
  point?: string;
}

export interface RunwayImpact {
  current_runway_months?: number;
  scenario_runway_months?: number | null;
  delta_months?: number | null;
  note?: string;
  [k: string]: unknown;
}

export interface Recommendation {
  decision?: string;
  confidence?: number;
  rationale?: string;
  key_risks?: string[];
  conditions?: string[];
  impact?: RunwayImpact;
}

export interface DebateState {
  decision?: string;
  phase?: string;
  positions?: TranscriptTurn[];
  transcript?: TranscriptTurn[];
  recommendation?: Recommendation;
}

export interface RosterMember {
  id: string;
  label: string;
  role: string;
  monogram: string;
  mandate?: string;
}
