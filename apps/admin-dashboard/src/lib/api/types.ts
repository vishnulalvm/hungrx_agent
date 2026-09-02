// Mirrors of the FastAPI backend's Pydantic schemas actually returned by
// the endpoints this dashboard calls. Kept hand-written (not generated)
// but 1:1 with core/schemas/* — see each type's comment for its source
// file. No business logic lives here, only shape.

export type Role = "SUPER_ADMIN" | "DATA_MANAGER" | "REVIEWER" | "VIEWER";

// core/schemas/user.py
export interface UserPublic {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  created_at: string;
}

// core/schemas/common.py
export interface PaginatedResponse<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}

// core/schemas/restaurant.py — RestaurantSummary (list rows)
export interface RestaurantSummary {
  id: string;
  name: string;
  is_active: boolean;
  city: string | null;
  menu_item_count: number;
  created_at: string | null;
}

// core/schemas/nutrition.py
export interface Macros {
  calories: string;
  protein_g?: string | null;
  carbs_g?: string | null;
  fat_g?: string | null;
  fiber_g?: string | null;
  sugar_g?: string | null;
  sodium_mg?: string | null;
}

export interface Nutrition {
  serving_size: string;
  macros: Macros;
  micronutrients?: Record<string, string> | null;
}

// core/schemas/menu.py
export interface Dish {
  id: string;
  category_id: string;
  name: string;
  description?: string | null;
  image_url?: string | null;
  nutrition?: Nutrition | null;
  allergens: string[];
  ingredients: { name: string; is_optional?: boolean }[];
  quantity?: string | null;
  price?: string | null;
  currency: string;
  review_state: "pending" | "confirmed" | "rejected";
}

export interface MenuCategory {
  id: string;
  menu_id?: string;
  parent_id?: string | null;
  name: string;
  display_order: number;
  dishes: Dish[];
  children: MenuCategory[];
}

export interface Menu {
  id: string;
  restaurant_id?: string;
  name: string;
  categories: MenuCategory[];
}

export interface RestaurantLocation {
  id: string;
  label?: string | null;
  address_line1: string;
  address_line2?: string | null;
  city: string;
  state?: string | null;
  postal_code?: string | null;
  country: string;
  latitude?: number | null;
  longitude?: number | null;
  phone?: string | null;
}

// core/schemas/restaurant.py — full Restaurant (detail page)
export interface Restaurant {
  id: string;
  name: string;
  description?: string | null;
  logo_url?: string | null;
  cover_image_url?: string | null;
  gallery_image_urls: string[];
  website_url?: string | null;
  cuisine_types: string[];
  locations: RestaurantLocation[];
  menus: Menu[];
  is_active: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

// core/schemas/ingestion.py
export interface IngestionTriggerRequest {
  name: string;
  city?: string | null;
  state?: string | null;
  country?: string | null;
  phone?: string | null;
}

export interface IngestionTriggerResult {
  job_id: string;
  restaurant_seed_id: string;
}

// core/schemas/proposed_change.py
export type ProposedChangeEntityType =
  "restaurant" | "restaurant_location" | "menu" | "menu_category" | "dish";

export type ProposedChangeStatus = "pending" | "approved" | "rejected" | "published";

// core/schemas/review.py
export interface ReviewSummary {
  id: string;
  entity_type: ProposedChangeEntityType;
  entity_id: string;
  status: ProposedChangeStatus;
  agent_run_id: string | null;
  created_at: string;
}

export interface ReviewDetail {
  id: string;
  entity_type: ProposedChangeEntityType;
  entity_id: string;
  status: ProposedChangeStatus;
  structured_json: Record<string, unknown>;
  validation_result: {
    is_valid: boolean;
    issues: { field_path: string; severity: "error" | "warning"; message: string }[];
  } | null;
  agent_run_id: string | null;
  source_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReviewDecisionRequest {
  reason?: string | null;
}

export interface ReviewEditRequest {
  edited_structured_json: Record<string, unknown>;
  reason?: string | null;
}

export interface ReviewActionResult {
  proposed_change_id: string;
  status: ProposedChangeStatus;
  published_restaurant_id?: string | null;
  errors: { node: string; message: string }[];
}

// core/schemas/agent_run.py
export type AgentWorkflowType = "collector_workflow" | "reviewer_workflow";
export type AgentRunStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled";

export interface AgentRun {
  id: string;
  workflow_type: AgentWorkflowType;
  restaurant_id: string | null;
  status: AgentRunStatus;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
  metrics: Record<string, unknown>;
}

// core/schemas/audit.py + audit_log.py
export type AuditAction =
  | "restaurant.create"
  | "restaurant.edit"
  | "restaurant.delete"
  | "ai.extraction"
  | "agent_run.trigger"
  | "proposed_change.create"
  | "proposed_change.edit"
  | "proposed_change.approve"
  | "proposed_change.reject"
  | "proposed_change.publish"
  | "security.login_success"
  | "security.login_failure"
  | "security.logout"
  | "security.logout_all"
  | "security.token_refresh";

export type AuditEntityType = "restaurant" | "proposed_change" | "agent_run" | "user" | "session";

export interface AuditLogEntry {
  id: string;
  action: AuditAction;
  entity_type: AuditEntityType;
  entity_id: string;
  actor_id: string | null;
  actor_email: string | null;
  old_values: Record<string, unknown> | null;
  new_values: Record<string, unknown> | null;
  agent_run_id: string | null;
  created_at: string;
}
