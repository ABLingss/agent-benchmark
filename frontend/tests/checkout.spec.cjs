const { test, expect } = require('@playwright/test');
const fs = require('node:fs');

async function add(page, id, times = 1) {
  for (let i = 0; i < times; i++) await page.getByTestId(`product-${id}`).getByRole('button', {name: '加入清单', exact: true}).click();
}
async function money(page, id, expected) {
  const text = await page.getByTestId(id).innerText();
  expect(Number(text.replace(/[^\d.-]/g, ''))).toBe(expected);
}

test.beforeEach(async ({page}) => { await page.goto('/'); });

test('空清单不能导出，金额为零', async ({page}) => {
  await expect(page.getByTestId('export-order')).toBeDisabled();
  for (const id of ['subtotal', 'discount', 'delivery', 'total']) await money(page, id, 0);
});

test('添加、修改数量、折扣与配送', async ({page}) => {
  await add(page, 'rice');
  await money(page, 'total', 21.90);
  const quantity = page.getByTestId('cart-rice').getByRole('spinbutton', {name: '数量', exact: true});
  await quantity.fill('3');
  await quantity.blur();
  await money(page, 'subtotal', 56.70);
  await money(page, 'discount', 8);
  await money(page, 'delivery', 0);
  await money(page, 'total', 48.70);
});

test('刷新保留，导出文件反映实际数据', async ({page}) => {
  await add(page, 'noodles', 2);
  await page.reload();
  await expect(page.getByTestId('cart-noodles').getByRole('spinbutton', {name: '数量', exact: true})).toHaveValue('2');
  const downloadEvent = page.waitForEvent('download');
  await page.getByTestId('export-order').click();
  const download = await downloadEvent;
  const data = JSON.parse(fs.readFileSync(await download.path(), 'utf8'));
  expect(data).toMatchObject({subtotal: 4580, discount: 800, delivery: 300, total: 4080});
  expect(data.items).toEqual([{id:'noodles', name:'番茄牛肉面', unitPrice:2290, quantity:2, lineTotal:4580}]);
});
