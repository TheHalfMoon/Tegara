import { test } from '@e2e-dev/web';
import { expect } from 'e2e';

test('explicitly shows research-only mode and disabled broker execution', async ({ app, screen }) => {
  await app.open('/');
  await expect(screen.getByText('LOCAL · RESEARCH ONLY')).toBeVisible();
  await expect(screen.getByText('NOT CONNECTED')).toBeVisible();
  await expect(screen.getByText('NO LIVE DATA')).toBeVisible();
  await expect(screen.getByText('Derayah orders')).toBeVisible();
});

test('calculates a fee-aware target and never claims executable quotes', async ({ app, screen }) => {
  await app.open('/');
  await expect(screen.getByRole('heading', {name: 'Option position'})).toBeVisible();
  await screen.getByRole('button', {name: /CALCULATE TARGET/}).click();
  await expect(screen.getByText('$1.65')).toBeVisible();
  await expect(screen.getByText('$40.42')).toBeVisible();
  await expect(screen.getByText(/not a live quote, executable order/i)).toBeVisible();
});

test('invalid numeric inputs cannot produce a target', async ({ app, screen }) => {
  await app.open('/');
  await screen.getByLabel('Open contracts').fill('0');
  await screen.getByRole('button', {name: /CALCULATE TARGET/}).click();
  await expect(screen.getByText('Your estimate starts here.')).toBeVisible();
});
