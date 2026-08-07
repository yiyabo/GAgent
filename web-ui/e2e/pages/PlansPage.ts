import { type Page } from '@playwright/test';

/**
 * Page object model for the plan side-panel on the chat page.
 *
 * The standalone /plans route was removed from the SPA; plan visualization
 * now lives in the right-hand tab panel of /chat ("Plan" / "Execution
 * Status" / "Artifacts" / "Agent Work").
 */
export class PlansPage {
  constructor(private page: Page) {}

  /** The plan panel is part of the chat page; nothing to navigate to. */
  async navigate(): Promise<void> {
    if (!this.page.url().includes('/chat')) {
      await this.page.goto('/chat');
    }
  }

  /** Return `true` when the plan tab panel is rendered. */
  async isPanelLoaded(): Promise<boolean> {
    const tablist = this.page.getByRole('tablist');
    try {
      await tablist.waitFor({ state: 'visible', timeout: 45000 });
      return true;
    } catch {
      return false;
    }
  }

  /** Visible tab names in the plan panel. */
  async getTabNames(): Promise<string[]> {
    return this.page.getByRole('tab').allInnerTexts();
  }

  /** Select a tab by name. */
  async openTab(name: string): Promise<void> {
    await this.page.getByRole('tab', { name }).click();
  }

  /** Text content of the currently active tab panel. */
  async getActivePanelText(): Promise<string> {
    const panel = this.page.locator('[role="tabpanel"]').first();
    return (await panel.innerText()) ?? '';
  }
}
