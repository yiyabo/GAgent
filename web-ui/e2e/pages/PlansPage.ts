import { type Page } from '@playwright/test';

/**
 * Page object model for the Plans page.
 *
 * Encapsulates selectors and interactions for the plan list, plan detail, and
 * plan DAG visualization rendered by `src/pages/Plans.tsx`.
 */
export class PlansPage {
  constructor(private page: Page) {}

  /** Navigate to the plans page. */
  async navigate(): Promise<void> {
    await this.page.goto('/plans');
  }

  /**
   * Return an array of plan titles from the plan selector dropdown.
   *
   * The Plans page uses an Ant Design `<Select>` for plan selection.
   * We open the dropdown, read the option labels, then close it.
   */
  async getPlansList(): Promise<string[]> {
    const select = this.page.locator('.ant-select');
    await select.first().click();

    // Ant Design renders dropdown options in a portal with class `.ant-select-item-option`
    const options = this.page.locator('.ant-select-item-option');
    await options.first().waitFor({ state: 'visible', timeout: 10000 }).catch(() => {});

    const count = await options.count();
    const titles: string[] = [];
    for (let i = 0; i < count; i++) {
      const text = await options.nth(i).textContent();
      if (text) {
        titles.push(text.trim());
      }
    }

    // Close the dropdown by pressing Escape
    await this.page.keyboard.press('Escape');
    return titles;
  }

  /**
   * Click the plan option at the given index in the dropdown.
   */
  async clickPlan(index: number): Promise<void> {
    const select = this.page.locator('.ant-select');
    await select.first().click();

    const options = this.page.locator('.ant-select-item-option');
    await options.first().waitFor({ state: 'visible', timeout: 10000 });
    await options.nth(index).click();
  }

  /**
   * Return the plan detail title from the Task Details card.
   * The selected plan's title is shown in the Descriptions component
   * under the "Task Name" label.
   */
  async getPlanDetailTitle(): Promise<string> {
    const titleCell = this.page.locator('.ant-descriptions-item-content').first();
    await titleCell.waitFor({ state: 'visible', timeout: 10000 });
    const text = await titleCell.textContent();
    return text?.trim() ?? '';
  }

  /**
   * Check whether the plan DAG / tree visualization container is visible.
   * The PlanDagVisualization component renders inside the main card.
   */
  async isPlanTreeVisible(): Promise<boolean> {
    // The DAG visualization is rendered as a canvas or SVG inside the plan card.
    // Look for the visualization container or the canvas element.
    const dagContainer = this.page.locator('.plan-dag-container, canvas, svg.react-flow__renderer');
    return dagContainer.first().isVisible({ timeout: 10000 }).catch(() => false);
  }
}
