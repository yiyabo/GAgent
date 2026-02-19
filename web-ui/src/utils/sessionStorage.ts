/**
 * SessionStorage 
 * sessionrelated localStorage 
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
  return localStorage.getItem(this.KEYS.CURRENT_SESSION_ID);
  } catch (error) {
  console.warn('Failed to get current session ID from localStorage:', error);
  return null;
  }
  }

  /**
  * sessionID
  */
  static setCurrentSessionId(sessionId: string): void {
  try {
  localStorage.setItem(this.KEYS.CURRENT_SESSION_ID, sessionId);
  } catch (error) {
  console.error('Failed to set current session ID to localStorage:', error);
  }
  }

  /**
  * sessionID
  */
  static clearCurrentSessionId(): void {
  try {
  localStorage.removeItem(this.KEYS.CURRENT_SESSION_ID);
  } catch (error) {
  console.error('Failed to clear current session ID from localStorage:', error);
  }
  }

  /**
  * getsessionID
  */
  static getAllSessionIds(): string[] {
  try {
  const idsStr = localStorage.getItem(this.KEYS.ALL_SESSION_IDS);
  if (!idsStr) return [];
  return JSON.parse(idsStr) as string[];
  } catch (error) {
  console.warn('Failed to get all session IDs from localStorage:', error);
  return [];
  }
  }

  /**
  * sessionID
  */
  static setAllSessionIds(sessionIds: string[]): void {
  try {
  localStorage.setItem(this.KEYS.ALL_SESSION_IDS, JSON.stringify(sessionIds));
  } catch (error) {
  console.error('Failed to set all session IDs to localStorage:', error);
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
  localStorage.removeItem(this.KEYS.ALL_SESSION_IDS);
  } catch (error) {
  console.error('Failed to clear all session data from localStorage:', error);
  }
  }

  /**
  * sessionIDmedium
  */
  static hasSessionId(sessionId: string): boolean {
  return this.getAllSessionIds().includes(sessionId);
  }
}
