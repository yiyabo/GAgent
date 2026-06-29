/**
 * SessionStorage 
 * sessionrelated sessionStorage 
 */

export class SessionStorage {
  private static readonly KEYS = {
  CURRENT_SESSION_ID: 'current_session_id',
  ALL_SESSION_IDS: 'all_session_ids',
  } as const;

  /**
  * getsessionID
  */
  static getCurrentSessionId(): string | null {
  try {
  return sessionStorage.getItem(this.KEYS.CURRENT_SESSION_ID);
  } catch (error) {
  console.warn('Failed to get current session ID from sessionStorage:', error);
  return null;
  }
  }

  /**
  * sessionID
  */
  static setCurrentSessionId(sessionId: string): void {
  try {
  sessionStorage.setItem(this.KEYS.CURRENT_SESSION_ID, sessionId);
  } catch (error) {
  console.error('Failed to set current session ID to sessionStorage:', error);
  }
  }

  /**
  * sessionID
  */
  static clearCurrentSessionId(): void {
  try {
  sessionStorage.removeItem(this.KEYS.CURRENT_SESSION_ID);
  } catch (error) {
  console.error('Failed to clear current session ID from sessionStorage:', error);
  }
  }

  /**
  * getsessionID
  */
  static getAllSessionIds(): string[] {
  try {
  const idsStr = sessionStorage.getItem(this.KEYS.ALL_SESSION_IDS);
  if (!idsStr) return [];
  return JSON.parse(idsStr) as string[];
  } catch (error) {
  console.warn('Failed to get all session IDs from sessionStorage:', error);
  return [];
  }
  }

  /**
  * sessionID
  */
  static setAllSessionIds(sessionIds: string[]): void {
  try {
  sessionStorage.setItem(this.KEYS.ALL_SESSION_IDS, JSON.stringify(sessionIds));
  } catch (error) {
  console.error('Failed to set all session IDs to sessionStorage:', error);
  }
  }

  /**
  * sessionID(ifdoes not exist)
  */
  static addSessionId(sessionId: string): void {
  const allIds = this.getAllSessionIds();
  if (!allIds.includes(sessionId)) {
  allIds.push(sessionId);
  this.setAllSessionIds(allIds);
  }
  }

  /**
  * mediumsessionID
  */
  static removeSessionId(sessionId: string): void {
  const allIds = this.getAllSessionIds();
  const filtered = allIds.filter(id => id !== sessionId);
  this.setAllSessionIds(filtered);
  }

  /**
  * sessionrelated
  */
  static clearAll(): void {
  this.clearCurrentSessionId();
  try {
  sessionStorage.removeItem(this.KEYS.ALL_SESSION_IDS);
  } catch (error) {
  console.error('Failed to clear all session data from sessionStorage:', error);
  }
  }

  /**
  * sessionIDmedium
  */
  static hasSessionId(sessionId: string): boolean {
  return this.getAllSessionIds().includes(sessionId);
  }
}
