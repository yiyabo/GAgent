/**
 * SessionStorage 工具类
 * 统一管理聊天会话相关的 localStorage 操作
 */

export class SessionStorage {
  // localStorage key 常量
  private static readonly KEYS = {
    CURRENT_SESSION_ID: 'current_session_id',
    ALL_SESSION_IDS: 'all_session_ids',
  } as const;

  /**
   * 获取当前会话ID
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
   * 设置当前会话ID
   */
  static setCurrentSessionId(sessionId: string): void {
    try {
      localStorage.setItem(this.KEYS.CURRENT_SESSION_ID, sessionId);
    } catch (error) {
      console.error('Failed to set current session ID to localStorage:', error);
    }
  }

  /**
   * 清除当前会话ID
   */
  static clearCurrentSessionId(): void {
    try {
      localStorage.removeItem(this.KEYS.CURRENT_SESSION_ID);
    } catch (error) {
      console.error('Failed to clear current session ID from localStorage:', error);
    }
  }

  /**
   * 获取所有会话ID列表
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
   * 设置所有会话ID列表
   */
  static setAllSessionIds(sessionIds: string[]): void {
    try {
      localStorage.setItem(this.KEYS.ALL_SESSION_IDS, JSON.stringify(sessionIds));
    } catch (error) {
      console.error('Failed to set all session IDs to localStorage:', error);
    }
  }

  /**
   * 添加会话ID到列表（如果不存在）
   */
  static addSessionId(sessionId: string): void {
    const allIds = this.getAllSessionIds();
    if (!allIds.includes(sessionId)) {
      allIds.push(sessionId);
      this.setAllSessionIds(allIds);
    }
  }

  /**
   * 从列表中移除会话ID
   */
  static removeSessionId(sessionId: string): void {
    const allIds = this.getAllSessionIds();
    const filtered = allIds.filter(id => id !== sessionId);
    this.setAllSessionIds(filtered);
  }

  /**
   * 清除所有会话相关数据
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
   * 检查会话ID是否存在于列表中
   */
  static hasSessionId(sessionId: string): boolean {
    return this.getAllSessionIds().includes(sessionId);
  }
}
