import { expect, test } from '@playwright/test';
import { cors, grantedProfile, mockSignInAndMe } from './support/fixtures';

// The handover deliberately excludes partial workflows; their original files
// and tests remain untouched in the development workspace.
for (const route of [
  '/app/master-data/organization', '/app/inventory/issues',
  '/app/inventory/transfers', '/app/inventory/counts', '/app/inventory/reports',
  '/app/manufacturing/compounds',
  '/app/manufacturing/batches/new', '/app/manufacturing/equipment',
  '/app/manufacturing/work-orders', '/app/quality/ncrs',
  '/app/quality/instruments', '/app/sales/prices', '/app/sales/quotes',
  '/app/sales/orders',
]) test(`handover excludes unfinished workflow ${route}`, async ({ page }) => {
  const unexpected: string[] = [];
  await page.route('**/api/v1/**', request => {
    unexpected.push(`${request.request().method()} ${new URL(request.request().url()).pathname}`);
    return request.fulfill({ status: 404, headers: cors, json: { detail: 'Unexpected feature request' } });
  });
  await mockSignInAndMe(page, { status: 200, body: {
    ...grantedProfile, modules: ['home', 'inventory', 'manufacturing', 'quality', 'sales'],
    permissions: ['org.manage', 'sales.order', 'sales.quote', 'route.move', 'qa.approve'], all_plants: true,
  } });
  await page.goto(route);
  await expect(page.getByRole('heading', { name: 'Not included in this handover', exact: true })).toBeVisible();
  expect(unexpected).toEqual([]);
});
