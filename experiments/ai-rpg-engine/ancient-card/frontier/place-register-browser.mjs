import assert from 'node:assert/strict';

export async function checkPlaceRegister(page,browser,submit,openMap){
  const current=page.getByLabel('台湾各地登记');
  await submit('朕下旨经略东番，命郑芝龙赴大员办理接收。',/待你签发/);
  await submit('签发这份旨意',/等候当地回文/);
  await submit('台湾现在谁管？请分地说明。',/大员，簿上登记为荷兰东印度公司/);
  await submit('推进至大员捷报抵京',/尚未核准变更/);
  await submit('核对大员交割',/待你确认/);
  assert.match(await current.innerText(),/大员 · 荷兰东印度公司/);
  await submit('确认这份交割',/大员由荷兰东印度公司改登为明/);
  if(process.env.ANCIENT_STORY_MAP==='1'){
    await submit('接着写朝议',/核准交割.*大员/);
    await submit('阁臣今日饮茶如何？',/记在案旁/);
    await submit('东番现在各归谁？',/大员，簿上登记为明/);
    await submit('记下传闻：商人说鸡笼换了旗，尚待核实。',/书记注明来处/);
    assert.match(await current.innerText(),/鸡笼 · 西班牙/);
    await submit('接着写朝议',/未经核实的转述/);
  }
  const saved=page.url();
  await page.close();
  const reopened=await browser.newPage();
  await reopened.goto(saved);
  const summary=reopened.getByLabel('台湾各地登记');
  assert.match(await summary.innerText(),/大员 · 明.*鸡笼 · 西班牙.*台湾原住民各社 · 各社自治/);
  const count=await reopened.locator('.conversation article .model-output').count();
  await reopened.getByRole('textbox',{name:'对话输入'}).fill('读档以后再查，台湾现在各地归谁？');
  await reopened.getByRole('button',{name:'发送',exact:true}).click();
  const output=reopened.locator('.conversation article .model-output').nth(count);
  await output.waitFor();
  assert.match(await output.innerText(),/大员，簿上登记为明[\s\S]*鸡笼，簿上登记为西班牙[\s\S]*各社，簿上登记为各社自治/);
  assert.equal(await reopened.locator('dialog.ming-atlas[open]').count(),0);
  const map=await openMap(reopened);
  await map.getByLabel('查阅地点').selectOption('tayouan');
  assert.equal(await map.locator('[data-reference-city=tayouan]').getAttribute('data-holder'),'明');
  assert.equal(await map.locator('[data-range=dutch]').getAttribute('data-holder'),'明');
  assert.equal(await map.locator('[data-range=spanish]').getAttribute('data-holder'),'西班牙');
  assert.match(await map.locator('.atlas-holder').innerText(),/明/);
  assert.match(await map.locator('.atlas-basis').innerText(),/荷兰东印度公司改登为明/);
  console.log(JSON.stringify({url:saved,turns:count+1,verified:'自由输入、拟旨和签发、获报、交割、读档续问、据点及守方'}));
}
