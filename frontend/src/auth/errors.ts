export type AuthFlowErrorCode =
  | 'configuration'
  | 'invalid_credentials'
  | 'email_not_confirmed'
  | 'unauthorized'
  | 'inactive_profile'
  | 'network'
  | 'malformed_response'
  | 'server_error'
  | 'session_changed'
  | 'unknown'

export class AuthFlowError extends Error {
  readonly code: AuthFlowErrorCode

  constructor(code: AuthFlowErrorCode, message: string) {
    super(message)
    this.name = 'AuthFlowError'
    this.code = code
  }
}
