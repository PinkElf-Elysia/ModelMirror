// Build-only @oai/artifact-tool; tests read the frozen XLSX, not this builder.
import fs from 'node:fs/promises';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const output = process.argv[2];
if (!output) throw new Error('Expected an explicit output directory');
await fs.mkdir(output, { recursive: true });
const workbook = Workbook.create();
const sheets = [
  ['Inventory', [['Item', 'Count', 'Note'], ['Harbor', 42, null], ['Spare', null, 'Awaiting review'], [null, 0, 'Zero is a value']]],
  ['History', [['Revision', 'Approved'], ['R1', true], ['R2', false]]],
];
for (const [name, values] of sheets) {
  const sheet = workbook.worksheets.add(name);
  sheet.getRangeByIndexes(0, 0, values.length, values[0].length).values = values;
  sheet.getUsedRange().format.font = { name: 'Arial', size: 11 };
  sheet.getUsedRange().format.columnWidth = 26;
  sheet.getUsedRange().format.rowHeight = 22;
  sheet.getRangeByIndexes(0, 0, 1, values[0].length).format.font = { name: 'Arial', size: 11, bold: true };
  sheet.showGridLines = false;
}
workbook.recalculate();
for (const [name] of sheets) {
  console.log((await workbook.inspect({ kind: 'table', range: `${name}!A1:C5`, include: 'values,formulas', tableMaxRows: 5, tableMaxCols: 3 })).ndjson);
  const preview = await workbook.render({ sheetName: name, range: 'A1:C5', scale: 1.5 });
  await fs.writeFile(`${output}/${name}.png`, new Uint8Array(await preview.arrayBuffer()));
}
console.log((await workbook.inspect({ kind: 'match', searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!', options: { useRegex: true, maxResults: 10 } })).ndjson);
await (await SpreadsheetFile.exportXlsx(workbook)).save(`${output}/multiple_sheets.xlsx`);
