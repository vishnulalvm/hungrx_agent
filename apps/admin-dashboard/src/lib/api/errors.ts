// Mirrors apps/api/app/core/errors.py's envelope: every non-2xx FastAPI
// response (AppError subclasses, validation errors, unhandled
// exceptions) is {error: {code, message, field}, request_id}.

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    field?: string | null;
  };
  request_id?: string | null;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly field: string | null;
  readonly requestId: string | null;

  constructor(status: number, body: ApiErrorBody) {
    super(body.error.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.error.code;
    this.field = body.error.field ?? null;
    this.requestId = body.request_id ?? null;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}
